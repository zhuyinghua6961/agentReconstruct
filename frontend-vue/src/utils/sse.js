export async function streamSseJson({ response, onEvent }) {
  const reader = response.body?.getReader();
  if (!reader) {
    throw new Error('Streaming not supported in current environment.');
  }

  const decoder = new TextDecoder('utf-8');
  let buffer = '';

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split('\n\n');
    buffer = frames.pop() || '';

    for (const frame of frames) {
      const dataLine = frame
        .split('\n')
        .map((line) => line.trim())
        .find((line) => line.startsWith('data:'));

      if (!dataLine) continue;

      const jsonText = dataLine.slice(5).trim();
      if (!jsonText) continue;

      try {
        onEvent(JSON.parse(jsonText));
      } catch (error) {
        onEvent({ type: 'error', error: `Invalid SSE payload: ${String(error)}` });
      }
    }
  }
}
