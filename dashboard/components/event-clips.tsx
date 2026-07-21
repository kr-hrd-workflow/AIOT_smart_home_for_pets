"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import type {
  PetCareClip,
  PetCareRemoteClient,
  PetCareRemoteMedia,
} from "../lib/petcare-remote";

export function EventClips({
  client,
  media,
}: {
  client: PetCareRemoteClient;
  media: PetCareRemoteMedia;
}) {
  const [clips, setClips] = useState<PetCareClip[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const deletingRef = useRef(false);
  const load = useCallback(async () => {
    try {
      setClips(await client.getClips());
    } catch {
      setError("클립 목록을 불러오지 못했습니다.");
    }
  }, [client]);

  useEffect(() => {
    let active = true;
    void client.getClips().then(
      (items) => {
        if (active) setClips(items);
      },
      () => {
        if (active) setError("클립 목록을 불러오지 못했습니다.");
      },
    );
    return () => {
      active = false;
    };
  }, [client]);

  const remove = async (item: PetCareClip) => {
    if (deletingRef.current) return;
    if (!window.confirm("이 이벤트 클립을 삭제하시겠습니까?")) return;
    deletingRef.current = true;
    setDeletingId(item.id);
    setError(null);
    try {
      await client.deleteClip(item.id);
      await load();
    } catch {
      setError("클립을 삭제하지 못했습니다. 다시 시도하세요.");
    } finally {
      deletingRef.current = false;
      setDeletingId(null);
    }
  };

  return (
    <section className="clip-section" aria-labelledby="clips-title">
      <header className="section-heading">
        <h2 id="clips-title">이벤트 클립</h2>
        <span>{clips.length}개</span>
      </header>
      {error && <p role="alert">{error}</p>}
      <ol className="clip-list">
        {clips.map((item) => (
          <li key={item.id}>
            <video
              aria-label={`이벤트 클립 ${item.started_at}`}
              controls
              preload="metadata"
              src={media.clipUrl(item.id)}
            />
            <div>
              <strong>{item.event_types.join(", ")}</strong>
              <time dateTime={item.started_at}>{item.started_at}</time>
              <p>
                만료: <time dateTime={item.expires_at}>{item.expires_at}</time>
              </p>
              <button
                className="destructive-delete"
                type="button"
                disabled={deletingId === item.id}
                aria-busy={deletingId === item.id}
                aria-label={`이벤트 클립 삭제 ${item.started_at}`}
                onClick={() => void remove(item)}
              >
                삭제
              </button>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
