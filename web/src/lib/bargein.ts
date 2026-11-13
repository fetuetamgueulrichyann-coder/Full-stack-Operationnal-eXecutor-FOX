/**
 * Interruption de FOX : pendant qu'il parle, le micro surveille le volume ; si l'utilisateur parle assez fort
 * assez longtemps, la voix s'arrête et l'écoute reprend. L'annulation d'écho du navigateur est demandée, mais
 * sans casque une voix très forte des haut-parleurs peut se faire passer pour l'utilisateur : d'où le réglage.
 */
export interface Detector {
  push(level: number, now: number): boolean;
}

export interface DetectorOptions {
  calibrateMs?: number;
  minLevel?: number;
  factor?: number;
  holdMs?: number;
}

/** Détecteur pur (sans micro) : calibre le bruit ambiant et l'écho, puis déclenche sur une parole soutenue. */
export function createDetector({ calibrateMs = 400, minLevel = 0.12, factor = 2.5, holdMs = 320 }: DetectorOptions = {}): Detector {
  let startedAt: number | null = null;
  let baseline = 0;
  let aboveSince: number | null = null;
  return {
    push(level, now) {
      if (startedAt === null) startedAt = now;
      if (now - startedAt < calibrateMs) {
        baseline = Math.max(baseline, level);
        return false;
      }
      const threshold = Math.max(minLevel, baseline * factor);
      if (level <= threshold) {
        aboveSince = null;
        return false;
      }
      if (aboveSince === null) aboveSince = now;
      return now - aboveSince >= holdMs;
    },
  };
}

const noop = () => undefined;

/** Ouvre le micro et appelle `onSpeech` une fois si l'utilisateur parle. Rend la fonction qui coupe le micro. */
export async function startBargeIn(onSpeech: () => void): Promise<() => void> {
  if (typeof navigator === "undefined" || !navigator.mediaDevices?.getUserMedia) return noop;
  let stream: MediaStream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: false },
    });
  } catch {
    return noop; // micro refusé : pas d'interruption, la conversation continue normalement
  }
  const w = window as unknown as { AudioContext?: typeof AudioContext; webkitAudioContext?: typeof AudioContext };
  const Ctor = w.AudioContext ?? w.webkitAudioContext;
  if (!Ctor) {
    stream.getTracks().forEach((t) => t.stop());
    return noop;
  }
  const ctx = new Ctor();
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 512;
  ctx.createMediaStreamSource(stream).connect(analyser); // pas relié aux haut-parleurs
  const buf = new Uint8Array(analyser.fftSize);
  const detector = createDetector();
  let done = false;
  const stop = () => {
    if (done) return;
    done = true;
    clearInterval(timer);
    stream.getTracks().forEach((t) => t.stop());
    void ctx.close().catch(() => undefined);
  };
  const timer = setInterval(() => {
    analyser.getByteTimeDomainData(buf);
    let sum = 0;
    for (let i = 0; i < buf.length; i++) {
      const v = (buf[i] - 128) / 128;
      sum += v * v;
    }
    if (detector.push(Math.min(1, Math.sqrt(sum / buf.length) * 3.2), performance.now())) {
      stop();
      onSpeech();
    }
  }, 50);
  return stop;
}
