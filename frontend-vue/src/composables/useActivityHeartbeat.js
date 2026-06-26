import { onMounted, onUnmounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { readStoredToken } from '../services/auth'
import { sendActivityHeartbeat } from '../services/activity'

const HEARTBEAT_INTERVAL_MS = 60_000
const IDLE_TIMEOUT_MS = 15 * 60 * 1000
const IDLE_CHECK_INTERVAL_MS = 30_000
const INTERACTION_PULSE_MS = 5_000
const LEADER_STORAGE_KEY = 'agentcode.activity.leader.v1'
const LEADER_STALE_MS = 90_000
const LEADER_CHANNEL = 'agentcode.activity.leader.v1'

function readTabId() {
  const key = 'agentcode.activity.tab_id.v1'
  const existing = String(sessionStorage.getItem(key) || '').trim()
  if (existing) {
    return existing
  }
  const generated = (typeof crypto !== 'undefined' && crypto.randomUUID)
    ? crypto.randomUUID()
    : `tab-${Date.now()}-${Math.random().toString(36).slice(2)}`
  sessionStorage.setItem(key, generated)
  return generated
}

function readLeaderRecord() {
  try {
    const raw = localStorage.getItem(LEADER_STORAGE_KEY)
    if (!raw) {
      return null
    }
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === 'object' ? parsed : null
  } catch {
    return null
  }
}

function writeLeaderRecord(tabId) {
  localStorage.setItem(
    LEADER_STORAGE_KEY,
    JSON.stringify({ tabId, ts: Date.now() }),
  )
}

export function useActivityHeartbeat() {
  const route = useRoute()
  const tabId = readTabId()
  let timerId = null
  let idleTimerId = null
  let started = false
  let isLeader = false
  let leaderChannel = null
  let finalizeSent = false
  let lastInteractionAt = 0
  let interactionPulseTimerId = null

  function shouldRun() {
    return Boolean(readStoredToken()) && Boolean(route.meta?.requiresAuth)
  }

  function hasRecentInteraction() {
    if (!lastInteractionAt) {
      return false
    }
    return Date.now() - lastInteractionAt < IDLE_TIMEOUT_MS
  }

  function refreshLeaderClaim() {
    const leader = readLeaderRecord()
    const now = Date.now()
    if (!leader?.tabId || now - Number(leader.ts || 0) > LEADER_STALE_MS) {
      writeLeaderRecord(tabId)
      isLeader = true
      return true
    }
    isLeader = leader.tabId === tabId
    if (isLeader) {
      writeLeaderRecord(tabId)
    }
    return isLeader
  }

  function syncServerInteraction(serverInteractionMs) {
    if (Number(serverInteractionMs) > lastInteractionAt) {
      lastInteractionAt = Number(serverInteractionMs)
    }
  }

  function markInteraction() {
    if (!shouldRun()) {
      return
    }
    lastInteractionAt = Date.now()
    if (!refreshLeaderClaim()) {
      return
    }
    if (interactionPulseTimerId !== null) {
      return
    }
    interactionPulseTimerId = window.setTimeout(() => {
      interactionPulseTimerId = null
      if (hasRecentInteraction() && document.visibilityState === 'visible') {
        void beat()
      }
    }, INTERACTION_PULSE_MS)
  }

  function shouldSendHeartbeat({ finalize = false } = {}) {
    if (!shouldRun() && !finalize) {
      return false
    }
    if (finalize) {
      return refreshLeaderClaim() || isLeader
    }
    if (!refreshLeaderClaim() || document.visibilityState !== 'visible') {
      return false
    }
    return hasRecentInteraction()
  }

  async function beat({ finalize = false } = {}) {
    if (!shouldSendHeartbeat({ finalize })) {
      return
    }
    if (finalize) {
      if (finalizeSent) {
        return
      }
      finalizeSent = true
    }
    try {
      const result = await sendActivityHeartbeat({
        finalize,
        lastInteractionAt,
      })
      syncServerInteraction(result?.serverInteractionMs)
      if (!finalize && isLeader) {
        writeLeaderRecord(tabId)
      }
    } catch {
      if (finalize) {
        finalizeSent = false
      }
    }
  }

  function stopTimer() {
    if (timerId !== null) {
      clearInterval(timerId)
      timerId = null
    }
    if (idleTimerId !== null) {
      clearInterval(idleTimerId)
      idleTimerId = null
    }
    if (interactionPulseTimerId !== null) {
      clearTimeout(interactionPulseTimerId)
      interactionPulseTimerId = null
    }
  }

  function startTimer() {
    stopTimer()
    if (!shouldRun() || document.visibilityState !== 'visible' || !refreshLeaderClaim()) {
      return
    }
    timerId = setInterval(() => {
      if (!hasRecentInteraction()) {
        void beat({ finalize: true })
        stopTimer()
        return
      }
      void beat()
    }, HEARTBEAT_INTERVAL_MS)
    idleTimerId = setInterval(() => {
      if (!hasRecentInteraction() && document.visibilityState === 'visible') {
        void beat({ finalize: true })
        stopTimer()
      }
    }, IDLE_CHECK_INTERVAL_MS)
  }

  function handleVisibilityChange() {
    if (document.visibilityState === 'visible') {
      refreshLeaderClaim()
      if (hasRecentInteraction()) {
        void beat()
        startTimer()
      }
      return
    }
    stopTimer()
    void beat({ finalize: true })
  }

  function handlePageHide(event) {
    if (event?.persisted) {
      return
    }
    void beat({ finalize: true })
  }

  function handleLeaderChannelMessage(event) {
    const data = event?.data
    if (!data || data.type !== 'leader-claim' || data.tabId === tabId) {
      return
    }
    const leader = readLeaderRecord()
    if (leader?.tabId === tabId) {
      isLeader = false
      stopTimer()
    }
  }

  function bindInteractionListeners() {
    const options = { passive: true }
    window.addEventListener('pointerdown', markInteraction, options)
    window.addEventListener('keydown', markInteraction, options)
    window.addEventListener('scroll', markInteraction, options)
    window.addEventListener('touchstart', markInteraction, options)
    return () => {
      window.removeEventListener('pointerdown', markInteraction, options)
      window.removeEventListener('keydown', markInteraction, options)
      window.removeEventListener('scroll', markInteraction, options)
      window.removeEventListener('touchstart', markInteraction, options)
    }
  }

  let unbindInteractionListeners = () => {}

  onMounted(() => {
    if (started) {
      return
    }
    started = true
    if (typeof BroadcastChannel !== 'undefined') {
      leaderChannel = new BroadcastChannel(LEADER_CHANNEL)
      leaderChannel.addEventListener('message', handleLeaderChannelMessage)
    }
    refreshLeaderClaim()
    if (isLeader && typeof BroadcastChannel !== 'undefined') {
      leaderChannel?.postMessage({ type: 'leader-claim', tabId })
    }
    unbindInteractionListeners = bindInteractionListeners()
    document.addEventListener('visibilitychange', handleVisibilityChange)
    window.addEventListener('pagehide', handlePageHide)
  })

  onUnmounted(() => {
    stopTimer()
    unbindInteractionListeners()
    document.removeEventListener('visibilitychange', handleVisibilityChange)
    window.removeEventListener('pagehide', handlePageHide)
    if (leaderChannel) {
      leaderChannel.removeEventListener('message', handleLeaderChannelMessage)
      leaderChannel.close()
      leaderChannel = null
    }
    void beat({ finalize: true })
    started = false
  })

  watch(
    () => [route.fullPath, Boolean(route.meta?.requiresAuth), readStoredToken()],
    () => {
      if (!shouldRun()) {
        stopTimer()
        return
      }
      refreshLeaderClaim()
      if (hasRecentInteraction()) {
        void beat()
        startTimer()
      }
    },
  )
}
