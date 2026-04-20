import { computed, ref } from 'vue';
import { getCurrentUser, loginAuth, registerAuth } from '../../../api/auth.js';

const TOKEN_KEY = 'agentcode.auth.token.v1';
const USER_KEY = 'agentcode.auth.user.v1';

const tokenRef = ref('');
const userRef = ref(null);
const authLoadingRef = ref(false);
const authErrorRef = ref('');

function loadStoredAuth() {
  if (typeof window === 'undefined') {
    return;
  }
  tokenRef.value = window.localStorage.getItem(TOKEN_KEY) || '';
  const rawUser = window.localStorage.getItem(USER_KEY);
  if (!rawUser) {
    userRef.value = null;
    return;
  }
  try {
    userRef.value = JSON.parse(rawUser);
  } catch {
    userRef.value = null;
  }
}

function persistAuth() {
  if (typeof window === 'undefined') {
    return;
  }
  if (tokenRef.value) {
    window.localStorage.setItem(TOKEN_KEY, tokenRef.value);
  } else {
    window.localStorage.removeItem(TOKEN_KEY);
  }
  if (userRef.value) {
    window.localStorage.setItem(USER_KEY, JSON.stringify(userRef.value));
  } else {
    window.localStorage.removeItem(USER_KEY);
  }
}

async function hydrateCurrentUser() {
  if (!tokenRef.value) {
    userRef.value = null;
    persistAuth();
    return false;
  }
  authLoadingRef.value = true;
  authErrorRef.value = '';
  try {
    const resp = await getCurrentUser();
    if (resp?.success && resp?.data) {
      userRef.value = resp.data;
      persistAuth();
      return true;
    }
    tokenRef.value = '';
    userRef.value = null;
    persistAuth();
    authErrorRef.value = resp?.error || '鉴权失败';
    return false;
  } catch (error) {
    tokenRef.value = '';
    userRef.value = null;
    persistAuth();
    authErrorRef.value = String(error);
    return false;
  } finally {
    authLoadingRef.value = false;
  }
}

export function useAuthSession() {
  if (!tokenRef.value && userRef.value === null) {
    loadStoredAuth();
  }

  const isAuthenticated = computed(() => Boolean(tokenRef.value && userRef.value));

  async function login({ username, password }) {
    authLoadingRef.value = true;
    authErrorRef.value = '';
    try {
      const resp = await loginAuth(username, password);
      if (resp?.success && resp?.data?.token) {
        tokenRef.value = resp.data.token;
        userRef.value = resp.data.user || null;
        persistAuth();
        await hydrateCurrentUser();
        return { success: true };
      }
      authErrorRef.value = resp?.error || '登录失败';
      return { success: false, error: authErrorRef.value };
    } catch (error) {
      authErrorRef.value = String(error);
      return { success: false, error: authErrorRef.value };
    } finally {
      authLoadingRef.value = false;
    }
  }

  async function register(payload) {
    authLoadingRef.value = true;
    authErrorRef.value = '';
    try {
      const resp = await registerAuth(payload);
      if (resp?.success && resp?.data?.token) {
        tokenRef.value = resp.data.token;
        userRef.value = resp.data.user || null;
        persistAuth();
        await hydrateCurrentUser();
        return { success: true };
      }
      authErrorRef.value = resp?.error || '注册失败';
      return { success: false, error: authErrorRef.value };
    } catch (error) {
      authErrorRef.value = String(error);
      return { success: false, error: authErrorRef.value };
    } finally {
      authLoadingRef.value = false;
    }
  }

  function logout() {
    tokenRef.value = '';
    userRef.value = null;
    authErrorRef.value = '';
    persistAuth();
  }

  return {
    token: tokenRef,
    user: userRef,
    isAuthenticated,
    authLoading: authLoadingRef,
    authError: authErrorRef,
    hydrateCurrentUser,
    login,
    register,
    logout,
  };
}
