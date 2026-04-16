from app.core.spreadsheet import build_xlsx, load_rows


def test_load_rows_reads_utf8_sig_csv_headers_and_items():
    rows = load_rows(
        file_bytes=(
            b"\xef\xbb\xbfprimary_department_name,primary_status,secondary_department_name,secondary_status\n"
            b"\xe8\xae\xa1\xe7\xae\x97\xe6\x9c\xba\xe5\xad\xa6\xe9\x99\xa2,active,\xe8\xbd\xaf\xe4\xbb\xb6\xe5\xb7\xa5\xe7\xa8\x8b\xe7\xb3\xbb,active\n"
        ),
        ext="csv",
    )

    assert rows["columns"] == [
        "primary_department_name",
        "primary_status",
        "secondary_department_name",
        "secondary_status",
    ]
    assert rows["items"][0]["primary_department_name"] == "计算机学院"


def test_build_xlsx_and_load_rows_round_trip_headers_and_values():
    payload = build_xlsx(
        headers=["primary_department_name", "primary_status", "secondary_department_name", "secondary_status"],
        rows=[["计算机学院", "active", "软件工程系", "disabled"]],
        sheet_name="部门导入",
    )

    rows = load_rows(file_bytes=payload, ext="xlsx")

    assert rows["items"][0]["secondary_status"] == "disabled"
