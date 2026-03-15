import { ref, watch } from 'vue';

const STORAGE_KEY = 'agentcode.frontend.ask-preferences.v1';

function loadPreferences() {
  if (typeof window === 'undefined') {
    return { useGenerationDriven: true };
  }

  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      return { useGenerationDriven: true };
    }
    const parsed = JSON.parse(raw);
    return {
      useGenerationDriven:
        typeof parsed?.useGenerationDriven === 'boolean' ? parsed.useGenerationDriven : true,
    };
  } catch {
    return { useGenerationDriven: true };
  }
}

export function useAskPreferences() {
  const initial = loadPreferences();
  const useGenerationDriven = ref(initial.useGenerationDriven);

  watch(
    useGenerationDriven,
    (value) => {
      if (typeof window === 'undefined') {
        return;
      }
      try {
        window.localStorage.setItem(
          STORAGE_KEY,
          JSON.stringify({
            useGenerationDriven: Boolean(value),
          })
        );
      } catch {
        // Ignore localStorage write failures.
      }
    },
    { immediate: true }
  );

  return {
    useGenerationDriven,
  };
}
