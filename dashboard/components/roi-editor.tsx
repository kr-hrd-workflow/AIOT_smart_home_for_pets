"use client";

import { useEffect, useRef, useState } from "react";

import type { ZoneIn, ZoneName, ZoneOut } from "../lib/types";

function editable(zone: ZoneOut): ZoneIn {
  return {
    x1: zone.x1,
    y1: zone.y1,
    x2: zone.x2,
    y2: zone.y2,
    enabled: zone.enabled,
  };
}

function valid(zone: ZoneIn) {
  return (
    [zone.x1, zone.y1, zone.x2, zone.y2].every(Number.isInteger) &&
    0 <= zone.x1 && zone.x1 < zone.x2 && zone.x2 <= 640 &&
    0 <= zone.y1 && zone.y1 < zone.y2 && zone.y2 <= 480
  );
}

export function RoiEditor({
  zones,
  onSave,
}: {
  zones: [ZoneOut, ZoneOut];
  onSave: (zoneName: ZoneName, input: ZoneIn) => Promise<void>;
}) {
  const [drafts, setDrafts] = useState<Record<ZoneName, ZoneIn>>(() => ({
    food_bowl: editable(zones[0]),
    pet_bed: editable(zones[1]),
  }));
  const [saving, setSaving] = useState<ZoneName | null>(null);
  const [error, setError] = useState<string | null>(null);
  const dirty = useRef<Record<ZoneName, boolean>>({ food_bowl: false, pet_bed: false });

  useEffect(() => {
    setDrafts((current) => ({
      food_bowl: dirty.current.food_bowl ? current.food_bowl : editable(zones[0]),
      pet_bed: dirty.current.pet_bed ? current.pet_bed : editable(zones[1]),
    }));
  }, [zones]);

  const setNumber = (zoneName: ZoneName, key: "x1" | "y1" | "x2" | "y2", value: string) => {
    dirty.current[zoneName] = true;
    setDrafts((current) => ({
      ...current,
      [zoneName]: { ...current[zoneName], [key]: Number(value) },
    }));
  };

  const save = async (zoneName: ZoneName) => {
    const draft = drafts[zoneName];
    if (!valid(draft)) {
      setError("영역은 0–640 × 0–480 정수 범위 안에서 양의 크기여야 합니다.");
      return;
    }
    setSaving(zoneName);
    setError(null);
    try {
      await onSave(zoneName, draft);
      dirty.current[zoneName] = false;
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "영역을 저장하지 못했습니다.");
    } finally {
      setSaving(null);
    }
  };

  return (
    <div className="zone-table">
      {zones.map((zone) => {
        const label = zone.zone_name === "food_bowl" ? "급식 구역" : "침대 구역";
        const draft = drafts[zone.zone_name];
        return (
          <fieldset key={zone.zone_name} disabled={saving !== null}>
            <legend>{label}</legend>
            {(["x1", "y1", "x2", "y2"] as const).map((key) => (
              <label key={key}>
                <span>{key}</span>
                <input
                  aria-label={`${label} ${key}`}
                  type="number"
                  step="1"
                  min="0"
                  max={key.startsWith("x") ? "640" : "480"}
                  value={draft[key]}
                  onChange={(event) => setNumber(zone.zone_name, key, event.currentTarget.value)}
                />
              </label>
            ))}
            <label className="zone-toggle">
              <input
                type="checkbox"
                checked={draft.enabled}
                onChange={(event) => {
                  dirty.current[zone.zone_name] = true;
                  setDrafts((current) => ({
                    ...current,
                    [zone.zone_name]: {
                      ...current[zone.zone_name],
                      enabled: event.currentTarget.checked,
                    },
                  }));
                }}
              />
              사용
            </label>
            <button type="button" disabled={saving !== null} onClick={() => void save(zone.zone_name)}>
              {label} 저장
            </button>
          </fieldset>
        );
      })}
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
