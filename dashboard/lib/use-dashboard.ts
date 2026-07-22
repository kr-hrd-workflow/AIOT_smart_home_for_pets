"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { PetCareClient } from "./api-client";
import type {
  CalibrationUiState,
  DashboardData,
  DashboardMessage,
  ZoneIn,
  ZoneName,
} from "./types";

const idleCalibration: CalibrationUiState = {
  phase: "idle",
  code: null,
  channels: [],
  message: "빈 침대에서 60초 영점 보정을 시작할 수 있습니다.",
};
const INITIAL_RETRY_DELAY_MS = 1_000;

function applyMessage(current: DashboardData, message: DashboardMessage): DashboardData {
  if (message.type === "dashboard_update") {
    return { ...message.payload, zones: current.zones, calibration: current.calibration };
  }
  if (message.type === "bed_status") return { ...current, bed: message.payload };
  const anomalies = [
    message.payload,
    ...current.anomalies.filter((item) => item.id !== message.payload.id),
  ];
  return { ...current, anomalies };
}

export function useDashboard() {
  const [client] = useState(() => new PetCareClient());
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const mounted = useRef(false);
  const operations = useRef(new Set<AbortController>());

  useEffect(() => {
    mounted.current = true;
    let loadController: AbortController | undefined;
    let retry: ReturnType<typeof setTimeout> | undefined;
    const pendingMessages: DashboardMessage[] = [];

    const load = () => {
      const controller = new AbortController();
      loadController = controller;
      void Promise.all([
        client.getSummary(controller.signal),
        client.getZones(controller.signal),
      ])
        .then(([summary, zones]) => {
          if (!mounted.current || controller.signal.aborted) return;
          const initial: DashboardData = { ...summary, zones, calibration: idleCalibration };
          const current = pendingMessages.splice(0).reduce(applyMessage, initial);
          setData(current);
          setError(null);
        })
        .catch((caught: unknown) => {
          if (!mounted.current || controller.signal.aborted) return;
          controller.abort();
          setError(caught instanceof Error ? caught.message : "로컬 대시보드를 불러오지 못했습니다.");
          retry = setTimeout(load, INITIAL_RETRY_DELAY_MS);
        });
    };

    load();

    const unsubscribe = client.subscribe(
      (message) =>
        setData((current) => {
          if (current) return applyMessage(current, message);
          pendingMessages.push(message);
          return current;
        }),
      () => {
        if (mounted.current) setError("실시간 업데이트 형식이 올바르지 않습니다.");
      },
    );
    return () => {
      mounted.current = false;
      if (retry !== undefined) clearTimeout(retry);
      loadController?.abort();
      for (const controller of operations.current) controller.abort();
      operations.current.clear();
      unsubscribe();
    };
  }, [client]);

  const calibrate = useCallback(async () => {
    const controller = new AbortController();
    operations.current.add(controller);
    setData((current) =>
      current
        ? {
            ...current,
            calibration: {
              phase: "submitting",
              code: null,
              channels: [],
              message: "60초 영점 보정을 진행하고 있습니다.",
            },
          }
        : current,
    );
    try {
      const result = await client.calibrateBed(controller.signal);
      if (!mounted.current || controller.signal.aborted) return;
      setData((current) => {
        if (!current) return current;
        if (result.ok) {
          return {
            ...current,
            calibration: {
              phase: "success",
              code: null,
              channels: [],
              message: "침대 영점 보정이 완료되었습니다.",
            },
          };
        }
        return {
          ...current,
          calibration: {
            phase: "error",
            code: result.status === 409 ? result.error.code : null,
            channels: result.status === 409 ? result.error.channels : [],
            message: result.error.message,
          },
        };
      });
    } catch (caught) {
      if (!mounted.current || controller.signal.aborted) return;
      setData((current) =>
        current
          ? {
              ...current,
              calibration: {
                phase: "error",
                code: null,
                channels: [],
                message: caught instanceof Error ? caught.message : "침대 보정에 실패했습니다.",
              },
            }
          : current,
      );
    } finally {
      operations.current.delete(controller);
    }
  }, [client]);

  const updateZone = useCallback(
    async (zoneName: ZoneName, input: ZoneIn) => {
      const controller = new AbortController();
      operations.current.add(controller);
      try {
        const updated = await client.updateZone(zoneName, input, controller.signal);
        if (!mounted.current || controller.signal.aborted) return;
        setData((current) => {
          if (!current) return current;
          const zones = current.zones.map((zone) =>
            zone.zone_name === updated.zone_name ? updated : zone,
          ) as DashboardData["zones"];
          return { ...current, zones };
        });
      } finally {
        operations.current.delete(controller);
      }
    },
    [client],
  );

  return { data, error, calibrate, updateZone };
}
