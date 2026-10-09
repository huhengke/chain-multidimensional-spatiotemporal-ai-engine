import { useCallback, useEffect, useRef, useState } from "react";

import { fetchTask } from "../api/endpoints";
import type { ImportTaskInfo } from "../api/types";

const POLL_INTERVAL = 1500;
const MAX_RETRY_DELAY = 8000;

/**
 * 导入任务轮询：每 1.5s 拉取一次任务进度，结束（非 running/pending）后停止。
 * 数据导入页与数据集注册向导共用。
 */
export function useImportTask() {
  const [task, setTask] = useState<ImportTaskInfo | null>(null);
  const timer = useRef<number | null>(null);
  const generation = useRef(0);

  const stop = useCallback(() => {
    generation.current += 1;
    if (timer.current) {
      window.clearTimeout(timer.current);
      timer.current = null;
    }
  }, []);

  useEffect(() => stop, [stop]);

  const watch = useCallback(
    async (taskUuid: string, onDone?: (t: ImportTaskInfo) => void) => {
      stop();
      const watchGeneration = generation.current;
      let retryCount = 0;

      const schedule = (delay: number, poll: () => Promise<void>) => {
        if (watchGeneration !== generation.current) return;
        timer.current = window.setTimeout(() => void poll(), delay);
      };

      const poll = async () => {
        if (watchGeneration !== generation.current) return;
        try {
          const t = await fetchTask(taskUuid);
          if (watchGeneration !== generation.current) return;
          setTask(t);
          retryCount = 0;
          if (t.status === "running" || t.status === "pending") {
            schedule(POLL_INTERVAL, poll);
          } else {
            onDone?.(t);
          }
        } catch (error) {
          const status = (error as { response?: { status?: number } })
            .response?.status;
          if (status === 401 || status === 403 || status === 404) return;
          const delay = Math.min(
            POLL_INTERVAL * 2 ** Math.min(retryCount, 3),
            MAX_RETRY_DELAY
          );
          retryCount += 1;
          schedule(delay, poll);
        }
      };
      await poll();
    },
    [stop]
  );

  const reset = useCallback(() => {
    stop();
    setTask(null);
  }, [stop]);

  const running = task?.status === "running" || task?.status === "pending";
  const accounted = task
    ? task.succeeded + task.skipped + task.failed
    : 0;
  const percent = task
    ? !running
      ? 100
      : task.total > 0
        ? Math.min(99, Math.round((accounted / task.total) * 100))
        : 0
    : 0;

  return { task, percent, running, watch, reset, stop };
}
