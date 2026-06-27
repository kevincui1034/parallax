"use client";

/**
 * Speech-to-text for the agent composer.
 *
 * Uses the browser's built-in Web Speech API (SpeechRecognition) — zero deps,
 * no server round-trip, works in Chrome/Edge and Safari (webkit-prefixed).
 * This is the in-app dictation path: mic → live transcript → agent input.
 *
 * Note on Voice Cursor (voicecursor.ai): it's an OS-level dictation app, not a
 * web SDK — it types into whatever text field is focused, so it already works
 * on top of this composer with no code. This hook is the in-browser fallback
 * that needs no install. The `onFinal`/`interim` shape is backend-agnostic, so
 * a cloud STT (e.g. a GMI transcription endpoint) could replace the internals
 * without touching the UI.
 */

import { useCallback, useEffect, useRef, useState } from "react";

/* ---- minimal Web Speech API typings (not in lib.dom) ---- */
interface SpeechRecognitionResult {
  0: { transcript: string };
  isFinal: boolean;
}
interface SpeechRecognitionEvent {
  resultIndex: number;
  results: { length: number; [i: number]: SpeechRecognitionResult };
}
interface SpeechRecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  start(): void;
  stop(): void;
  abort(): void;
  onresult: ((e: SpeechRecognitionEvent) => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  onend: (() => void) | null;
}
type SpeechRecognitionCtor = new () => SpeechRecognitionLike;

function getRecognitionCtor(): SpeechRecognitionCtor | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as {
    SpeechRecognition?: SpeechRecognitionCtor;
    webkitSpeechRecognition?: SpeechRecognitionCtor;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export interface UseSpeechOptions {
  /** Fired with the live (interim) transcript as the user speaks. */
  onInterim?: (text: string) => void;
  /** Fired once with the finalized transcript when a phrase completes. */
  onFinal?: (text: string) => void;
  lang?: string;
}

export interface UseSpeech {
  /** True once we know the browser exposes SpeechRecognition. */
  supported: boolean;
  listening: boolean;
  error: string | null;
  toggle: () => void;
  stop: () => void;
}

export function useSpeech(opts: UseSpeechOptions = {}): UseSpeech {
  const { onInterim, onFinal, lang = "en-US" } = opts;
  const [supported, setSupported] = useState(false);
  const [listening, setListening] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const recRef = useRef<SpeechRecognitionLike | null>(null);
  // Keep callbacks current without re-creating the recognizer.
  const interimRef = useRef(onInterim);
  const finalRef = useRef(onFinal);
  useEffect(() => {
    interimRef.current = onInterim;
    finalRef.current = onFinal;
  });

  useEffect(() => {
    const Ctor = getRecognitionCtor();
    if (!Ctor) return;
    // One-time client capability check — can't run during SSR.
    /* eslint-disable-next-line react-hooks/set-state-in-effect -- intentional one-time client init */
    setSupported(true);

    const rec = new Ctor();
    rec.lang = lang;
    rec.continuous = true;
    rec.interimResults = true;

    rec.onresult = (e) => {
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        const text = r[0].transcript;
        if (r.isFinal) finalRef.current?.(text.trim());
        else interim += text;
      }
      if (interim) interimRef.current?.(interim);
    };
    rec.onerror = (e) => {
      // "aborted"/"no-speech" are benign stop conditions, not user-facing errors.
      if (e.error !== "aborted" && e.error !== "no-speech") setError(e.error);
      setListening(false);
    };
    rec.onend = () => setListening(false);

    recRef.current = rec;
    return () => {
      rec.onresult = null;
      rec.onerror = null;
      rec.onend = null;
      rec.abort();
      recRef.current = null;
    };
  }, [lang]);

  const stop = useCallback(() => {
    recRef.current?.stop();
    setListening(false);
  }, []);

  const toggle = useCallback(() => {
    const rec = recRef.current;
    if (!rec) return;
    if (listening) {
      rec.stop();
      setListening(false);
      return;
    }
    setError(null);
    try {
      rec.start();
      setListening(true);
    } catch {
      // start() throws if called while already started — ignore.
    }
  }, [listening]);

  return { supported, listening, error, toggle, stop };
}
