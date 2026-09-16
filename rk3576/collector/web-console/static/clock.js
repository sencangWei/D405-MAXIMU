'use strict';

(function (root) {
  function sampleSeconds(status) {
    const value = status.capture_elapsed_s ?? status.elapsed_s ?? 0;
    return Number.isFinite(value) ? Math.max(0, Number(value)) : 0;
  }

  function reconcile(previous, status, connected, nowMs) {
    if (!connected) return null;
    const seconds = sampleSeconds(status);
    const running = status.capture_running === true;
    const jobId = status.job_id ?? null;
    if (!running || !previous || !previous.running || previous.jobId !== jobId) {
      return {seconds, time: nowMs, running, jobId};
    }
    const carried = previous.seconds + Math.max(0, nowMs - previous.time) / 1000;
    return {seconds: Math.max(seconds, carried), time: nowMs, running, jobId};
  }

  function value(base, nowMs) {
    if (!base) return 0;
    return base.seconds + (base.running ? Math.max(0, nowMs - base.time) / 1000 : 0);
  }

  const api = {sampleSeconds, reconcile, value};
  root.UMIClock = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
