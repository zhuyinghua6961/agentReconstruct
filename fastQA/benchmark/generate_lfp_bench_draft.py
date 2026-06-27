#!/usr/bin/env python3
"""Generate LFP-Bench v1 draft (120 questions) from lfp_papers summary chunks."""

from __future__ import annotations

import json
import random
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import chromadb

SEED = 20260625
VEC_PATH = Path(__file__).resolve().parents[2] / "resource/fastqa/vector_database"
OUT_PATH = Path(__file__).resolve().parent / "lfp_bench_v1_draft.jsonl"
SEED_PATH = Path(__file__).resolve().parent / "pilot_seed.jsonl"

# Target mix (auto-generated portion fills remainder after pilot_seed)
N_TOTAL = 120
N_RETRIEVAL = 80
N_SYNTHESIS = 40
N_UNANSWERABLE = 20  # insufficient + hard_negative combined
N_HARD_NEGATIVE = 5

SPLIT_DEV = 20
SPLIT_TEST_I = 50
SPLIT_TEST_II = 50

LFP_RE = re.compile(r"LiFePO4|磷酸铁锂|LFP|lifepo4", re.I)
NUM_CAPACITY = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:mAh\s*g[⁻\-−]?¹|mAh/g|mAh\s*g-1|mAh\s*g\s*-\s*1)",
    re.I,
)
NUM_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
NUM_TEMP = re.compile(r"(\d+(?:\.\d+)?)\s*(?:°C|℃|K)")
NUM_CONC = re.compile(r"(\d+(?:\.\d+)?)\s*mol\s*L[⁻\-−]?¹", re.I)
NUM_RATE = re.compile(r"(\d+(?:\.\d+)?)\s*C\b")
NUM_CYCLES = re.compile(r"(\d+)\s*(?:次|cycles?|循环)", re.I)

TOPIC_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("recycling", re.compile(r"回收|再生|废旧|黑粉|spent|recycl", re.I)),
    ("carbon", re.compile(r"碳|CNT|石墨烯|包覆|carbon|graphene", re.I)),
    ("safety", re.compile(r"热失控|安全|thermal runaway|separator|隔膜", re.I)),
    ("rate", re.compile(r"倍率|rate|高倍|10C|5C", re.I)),
    ("doping", re.compile(r"掺杂|dop|Mn|V|Ti|Nb", re.I)),
    ("synthesis", re.compile(r"合成|sol-gel|水热|hydrothermal|freeze|冷冻|烧结", re.I)),
]

HARD_NEGATIVE_QUESTIONS = [
    {
        "question": "NCM811 正极在 4.3 V 下的首次库仑效率是多少？",
        "gold_answer": "语料为 LFP 专题库，无 NCM811 可靠数据，应拒答或说明证据不足",
        "check": "refuse",
    },
    {
        "question": "LiCoO2 在 4.5 V 高电压下 100 次循环的容量保持率是多少？",
        "gold_answer": "语料无 LiCoO2 高电压循环数据，应拒答",
        "check": "refuse",
    },
    {
        "question": "三元 NCA 软包电池在针刺测试中的最高表面温度是多少？",
        "gold_answer": "语料无 NCA 针刺测试数据，应拒答",
        "check": "refuse",
    },
    {
        "question": "LMFP（锰铁磷酸锂）在 0.1C 下的放电平台电压精确到小数点后两位是多少？",
        "gold_answer": "语料以 LiFePO4 为主，LMFP 专项数据不足，应拒答或谨慎说明",
        "check": "refuse",
    },
    {
        "question": "钠离子电池 Na3V2(PO4)3 在 5C 下的比容量是多少？",
        "gold_answer": "语料为 LFP 库，无 NVP 数据，应拒答",
        "check": "refuse",
    },
]

INSUFFICIENT_TEMPLATES = [
    ("在 -30°C 低温 1C 条件下的放电比容量是多少？", "文献未报道低温 1C 测试"),
    ("在 60°C 高温下循环 500 次后的容量保持率是多少？", "文献未报道该高温长循环条件"),
    ("在全固态电解质（如 LLZO）装配下的界面阻抗是多少？", "文献为液态电解液体系，未报道全固态界面阻抗"),
    ("在 5 MPa 外部压力下的锂离子扩散系数是多少？", "文献未给出压力依赖扩散系数"),
    ("在湿度 90% 环境下存放 30 天后的容量衰减率是多少？", "文献未报道高湿存储老化数据"),
    ("采用 5 V 高电压截止时的首周效率是多少？", "LFP 常规电压窗口 2.5–4.0/4.2 V，未报道 5 V 条件"),
    ("颗粒尺寸精确到 50 nm 时的 tap density 是多少？", "文献未给出该粒径对应振实密度"),
    ("在 10 A g⁻¹ 电流密度下的质量比容量是多少？", "文献未报道该极端电流密度"),
]


@dataclass
class PaperRecord:
    doi: str
    chunk0: str
    chunks: dict[int, str] = field(default_factory=dict)
    topics: set[str] = field(default_factory=set)

    @property
    def short_doi(self) -> str:
        return self.doi.split("_", 1)[-1] if "_" in self.doi else self.doi


def load_papers(limit_offset_scan: int = 35000, batch: int = 500) -> list[PaperRecord]:
    col = chromadb.PersistentClient(path=str(VEC_PATH)).get_collection("lfp_papers")
    by_doi: dict[str, PaperRecord] = {}
    offset = 0
    while offset < limit_offset_scan:
        got = col.get(limit=batch, offset=offset, include=["metadatas", "documents"])
        offset += batch
        docs = got.get("documents") or []
        metas = got.get("metadatas") or []
        if not docs:
            break
        for doc, meta in zip(docs, metas):
            doi = str(meta.get("doi") or "").strip()
            if not doi:
                continue
            text = (doc or "").strip()
            if not text:
                continue
            cid = int(meta.get("chunk_id") or 0)
            rec = by_doi.setdefault(doi, PaperRecord(doi=doi, chunk0=""))
            rec.chunks[cid] = text
            if cid == 0:
                rec.chunk0 = text
        if offset > 0 and offset % 5000 == 0:
            print(f"  scanned {offset} rows, unique dois {len(by_doi)}")

    papers = [p for p in by_doi.values() if p.chunk0 and LFP_RE.search(p.chunk0)]
    for p in papers:
        blob = " ".join(p.chunks.values())
        for name, pat in TOPIC_RULES:
            if pat.search(blob):
                p.topics.add(name)
    print(f"loaded {len(papers)} LFP papers with chunk0")
    return papers


def first_match(pattern: re.Pattern[str], text: str) -> str | None:
    m = pattern.search(text)
    return m.group(1) if m else None


def all_matches(pattern: re.Pattern[str], text: str, limit: int = 5) -> list[str]:
    return [m.group(1) for m in pattern.finditer(text)][:limit]


def make_numeric_q(p: PaperRecord, rng: random.Random) -> dict | None:
    text = p.chunk0
    caps = all_matches(NUM_CAPACITY, text, 3)
    if len(caps) >= 1:
        cap = caps[0]
        rates = all_matches(NUM_RATE, text, 2)
        rate_hint = f"{rates[0]}C" if rates else "报道倍率"
        q = f"文献 {p.short_doi} 中，LiFePO4 材料在 {rate_hint} 下的比容量约为多少 mAh g⁻¹？"
        return {
            "question": q,
            "gold_answer": f"{cap} mAh g⁻¹",
            "gold_evidence": text[:280],
            "check": f"numeric:{cap}",
            "subtype": "numeric_capacity",
        }
    pcts = all_matches(NUM_PERCENT, text, 2)
    if pcts:
        pct = pcts[0]
        q = f"文献 {p.short_doi} 报道的容量保持率（或相关百分比指标）约为多少？"
        return {
            "question": q,
            "gold_answer": f"{pct}%",
            "gold_evidence": text[:280],
            "check": f"numeric:{pct}",
            "subtype": "numeric_retention",
        }
    temps = all_matches(NUM_TEMP, text, 2)
    if temps:
        t = temps[0]
        q = f"文献 {p.short_doi} 涉及的关键温度条件是多少？"
        return {
            "question": q,
            "gold_answer": f"{t}（见原文温度单位）",
            "gold_evidence": text[:280],
            "check": f"contains:{t}",
            "subtype": "numeric_temperature",
        }
    return None


def make_fact_q(p: PaperRecord, rng: random.Random) -> dict | None:
    text = " ".join(p.chunks.get(i, "") for i in sorted(p.chunks))
    if "合成" in text or "synthes" in text.lower() or "制备" in text:
        chunk1 = p.chunks.get(1, p.chunk0)
        q = f"文献 {p.short_doi} 采用了什么制备/合成方法？简述关键步骤。"
        return {
            "question": q,
            "gold_answer": chunk1[:400],
            "gold_evidence": chunk1[:280],
            "check": "semantic:synthesis",
            "subtype": "fact_synthesis",
        }
    if p.topics & {"recycling"}:
        q = f"文献 {p.short_doi} 针对废旧 LiFePO4 的主要处理思路是什么？"
        ans = p.chunks.get(1, p.chunk0)
        return {
            "question": q,
            "gold_answer": ans[:400],
            "gold_evidence": ans[:280],
            "check": "semantic:recycling",
            "subtype": "fact_recycling",
        }
    chunk2 = p.chunks.get(2, "")
    if chunk2 and len(chunk2) > 40:
        q = f"文献 {p.short_doi} 研究要解决的核心问题是什么？"
        return {
            "question": q,
            "gold_answer": chunk2[:400],
            "gold_evidence": chunk2[:280],
            "check": "semantic:problem",
            "subtype": "fact_problem",
        }
    return None


def make_mechanism_q(p: PaperRecord, rng: random.Random) -> dict | None:
    chunk4 = p.chunks.get(4, "")
    if not chunk4 or len(chunk4) < 50:
        chunk4 = p.chunk0
    if p.topics & {"safety"}:
        q = f"文献 {p.short_doi} 中，LiFePO4/C 电池热失控或安全性的主要机理/因素是什么？"
    elif p.topics & {"carbon"}:
        q = f"文献 {p.short_doi} 中，碳材料或导电网络如何改善 LiFePO4 电化学性能？"
    elif p.topics & {"doping"}:
        q = f"文献 {p.short_doi} 中，掺杂或结构修饰如何影响 LiFePO4 性能？"
    else:
        q = f"文献 {p.short_doi} 中，性能提升的主要机理是什么？"
    return {
        "question": q,
        "gold_answer": chunk4[:450],
        "gold_evidence": chunk4[:280],
        "check": "semantic:mechanism",
        "subtype": "mechanism",
    }


def make_insufficient_q(p: PaperRecord, rng: random.Random) -> dict:
    tpl_q, tpl_a = rng.choice(INSUFFICIENT_TEMPLATES)
    q = f"根据文献 {p.short_doi}，{tpl_q}"
    return {
        "question": q,
        "gold_answer": tpl_a,
        "gold_evidence": "",
        "check": "refuse",
        "subtype": "insufficient_evidence",
        "answerable": False,
        "gold_doi": [],
    }


def make_synthesis_q(p1: PaperRecord, p2: PaperRecord, topic: str) -> dict:
    if topic == "recycling":
        q = (
            f"对比文献 {p1.short_doi} 与 {p2.short_doi}："
            "两者废旧 LFP 回收/再生路径有何异同？各报道的关键电化学指标是什么？"
        )
    elif topic == "carbon":
        q = (
            f"对比文献 {p1.short_doi} 与 {p2.short_doi}："
            "碳源或导电添加剂策略有何不同？对倍率/循环性能的影响如何？"
        )
    elif topic == "safety":
        q = (
            f"对比文献 {p1.short_doi} 与 {p2.short_doi}："
            "热安全或热失控讨论的重点有何差异？"
        )
    elif topic == "rate":
        q = (
            f"对比文献 {p1.short_doi} 与 {p2.short_doi}："
            "报道的高倍率性能数据分别是多少？"
        )
    else:
        q = (
            f"综合文献 {p1.short_doi} 与 {p2.short_doi}："
            "归纳两者在 LiFePO4 改性策略与主要性能结论上的异同。"
        )
    ans = (
        f"[{p1.doi}] {p1.chunk0[:200]} ... "
        f"[{p2.doi}] {p2.chunk0[:200]}"
    )
    return {
        "question": q,
        "gold_answer": ans,
        "gold_evidence": f"{p1.chunk0[:150]} | {p2.chunk0[:150]}",
        "gold_doi": [p1.doi, p2.doi],
        "check": "multi_doi:2",
        "subtype": f"synthesis_{topic}",
        "topic": topic,
    }


def assign_splits(items: list[dict], rng: random.Random) -> None:
    rng.shuffle(items)
    for i, item in enumerate(items):
        if i < SPLIT_DEV:
            item["split"] = "dev"
        elif i < SPLIT_DEV + SPLIT_TEST_I:
            item["split"] = "test_i"
        else:
            item["split"] = "test_ii"


def load_pilot_seed() -> list[dict]:
    if not SEED_PATH.exists():
        return []
    rows = []
    for line in SEED_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def build_questions(papers: list[PaperRecord], rng: random.Random) -> list[dict]:
    pilot = load_pilot_seed()
    pilot_ret = sum(1 for q in pilot if q["track"] == "retrieval")
    pilot_syn = sum(1 for q in pilot if q["track"] == "synthesis")
    pilot_dois = {d for q in pilot for d in q.get("gold_doi", [])}

    target_numeric = max(0, 25 - sum(1 for q in pilot if q.get("subtype", "").startswith("numeric")))
    target_fact = max(0, 20 - sum(1 for q in pilot if q.get("subtype", "").startswith("fact")))
    target_mech = max(0, 15 - sum(1 for q in pilot if q.get("subtype") == "mechanism"))
    pilot_unans = sum(1 for q in pilot if not q.get("answerable", True))
    pilot_insuf = sum(1 for q in pilot if q.get("subtype") == "insufficient_evidence")
    pilot_hn = sum(1 for q in pilot if q.get("subtype") == "hard_negative")

    target_unans_auto = max(0, N_UNANSWERABLE - pilot_unans)
    target_hn = min(max(0, N_HARD_NEGATIVE - pilot_hn), target_unans_auto)
    target_insuf = target_unans_auto - target_hn
    target_retrieval_auto = N_RETRIEVAL - pilot_ret
    target_synthesis_auto = N_SYNTHESIS - pilot_syn

    rng.shuffle(papers)
    papers = [p for p in papers if p.doi not in pilot_dois]
    questions: list[dict] = []
    used_doi_numeric: set[str] = set()
    used_doi_fact: set[str] = set()
    used_doi_mech: set[str] = set()
    used_doi_insuf: set[str] = set()

    # --- Retrieval auto ---
    target_numeric = target_numeric or 0
    target_fact = target_fact or 0
    target_mech = target_mech or 0

    for p in papers:
        if len([q for q in questions if q.get("subtype", "").startswith("numeric")]) >= target_numeric:
            break
        if p.doi in used_doi_numeric:
            continue
        item = make_numeric_q(p, rng)
        if item:
            used_doi_numeric.add(p.doi)
            questions.append({"track": "retrieval", "answerable": True, "gold_doi": [p.doi], **item})

    for p in papers:
        if len([q for q in questions if q.get("subtype", "").startswith("fact")]) >= target_fact:
            break
        if p.doi in used_doi_fact or p.doi in used_doi_numeric:
            continue
        item = make_fact_q(p, rng)
        if item:
            used_doi_fact.add(p.doi)
            questions.append({"track": "retrieval", "answerable": True, "gold_doi": [p.doi], **item})

    mech_papers = [p for p in papers if p.doi not in used_doi_numeric | used_doi_fact]
    rng.shuffle(mech_papers)
    for p in mech_papers:
        if len([q for q in questions if q.get("subtype") == "mechanism"]) >= target_mech:
            break
        if p.doi in used_doi_mech:
            continue
        item = make_mechanism_q(p, rng)
        used_doi_mech.add(p.doi)
        questions.append({"track": "retrieval", "answerable": True, "gold_doi": [p.doi], **item})

    insuf_pool = [p for p in papers if p.doi not in used_doi_insuf]
    rng.shuffle(insuf_pool)
    for p in insuf_pool[:target_insuf]:
        item = make_insufficient_q(p, rng)
        used_doi_insuf.add(p.doi)
        questions.append({"track": "retrieval", **item})

    for hn in HARD_NEGATIVE_QUESTIONS[:target_hn]:
        questions.append(
            {
                "track": "retrieval",
                "answerable": False,
                "gold_doi": [],
                "subtype": "hard_negative",
                **hn,
            }
        )

    # Trim/pad auto retrieval (always keep unanswerable items)
    retrieval_auto = [q for q in questions if q["track"] == "retrieval"]
    unanswerable_auto = [q for q in retrieval_auto if not q.get("answerable", True)]
    answerable_auto = [q for q in retrieval_auto if q.get("answerable", True)]
    if len(retrieval_auto) > target_retrieval_auto:
        drop = len(retrieval_auto) - target_retrieval_auto
        answerable_auto = answerable_auto[: max(0, len(answerable_auto) - drop)]
        retrieval_auto = unanswerable_auto + answerable_auto
    elif len(retrieval_auto) < target_retrieval_auto:
        extra = [p for p in papers if p.doi not in {d for q in retrieval_auto for d in q.get("gold_doi", [])}]
        for p in extra:
            if len(retrieval_auto) >= target_retrieval_auto:
                break
            item = make_numeric_q(p, rng) or make_fact_q(p, rng) or make_mechanism_q(p, rng)
            if item:
                retrieval_auto.append({"track": "retrieval", "answerable": True, "gold_doi": [p.doi], **item})
    questions = retrieval_auto

    # --- Synthesis: 40 cross-paper ---
    by_topic: dict[str, list[PaperRecord]] = defaultdict(list)
    for p in papers:
        for t in p.topics:
            by_topic[t].append(p)
    if not by_topic:
        by_topic["general"] = papers[:100]

    synthesis: list[dict] = []
    topic_cycle = ["recycling", "carbon", "safety", "rate", "doping", "synthesis", "general"]
    ti = 0
    while len(synthesis) < target_synthesis_auto:
        topic = topic_cycle[ti % len(topic_cycle)]
        ti += 1
        pool = by_topic.get(topic) or papers
        if len(pool) < 2:
            pool = papers
        p1, p2 = rng.sample(pool, 2)
        if p1.doi == p2.doi:
            continue
        syn = make_synthesis_q(p1, p2, topic if topic in by_topic else "general")
        syn["track"] = "synthesis"
        syn["answerable"] = True
        synthesis.append(syn)

    questions.extend(synthesis[:target_synthesis_auto])
    auto = questions
    questions = pilot + auto

    # Splits: pin pilot to dev, distribute auto across dev/test_i/test_ii → 20/50/50 total
    for q in pilot:
        q["split"] = "dev"
    dev_slots_left = max(0, SPLIT_DEV - len(pilot))
    rng.shuffle(auto)
    for i, q in enumerate(auto):
        if i < dev_slots_left:
            q["split"] = "dev"
        elif i < dev_slots_left + SPLIT_TEST_I:
            q["split"] = "test_i"
        else:
            q["split"] = "test_ii"

    # Assign IDs
    r_idx = s_idx = 1
    for q in questions:
        if q.get("id", "").startswith(("R-P", "S-P")):
            continue
        if q["track"] == "retrieval":
            q["id"] = f"R{r_idx:03d}"
            r_idx += 1
        else:
            q["id"] = f"S{s_idx:03d}"
            s_idx += 1

    return questions


def main() -> None:
    rng = random.Random(SEED)
    print("loading papers from chroma ...")
    papers = load_papers()
    print("building 120 questions ...")
    questions = build_questions(papers, rng)

    assert len(questions) == N_TOTAL, f"expected {N_TOTAL} got {len(questions)}"
    n_ret = sum(1 for q in questions if q["track"] == "retrieval")
    n_syn = sum(1 for q in questions if q["track"] == "synthesis")
    n_unans = sum(1 for q in questions if not q.get("answerable", True))
    assert n_ret == 80 and n_syn == 40

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as f:
        for q in questions:
            row = {
                "id": q["id"],
                "split": q["split"],
                "track": q["track"],
                "answerable": q.get("answerable", True),
                "subtype": q.get("subtype", ""),
                "question": q["question"],
                "gold_doi": q.get("gold_doi", []),
                "gold_answer": q["gold_answer"],
                "gold_evidence": q.get("gold_evidence", ""),
                "check": q.get("check", ""),
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"wrote {OUT_PATH}")
    print(f"  retrieval={n_ret} synthesis={n_syn} unanswerable={n_unans}")
    splits = defaultdict(int)
    for q in questions:
        splits[q["split"]] += 1
    print(f"  splits: {dict(splits)}")


if __name__ == "__main__":
    main()
