(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.createPersistQueue = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";
  return function createPersistQueue(saveFn, opts) {
    opts = opts || {};
    const debounceMs = opts.debounceMs || 800;
    const onSaved = opts.onSaved || function () {};
    const onFailed = opts.onFailed || function () {};
    const pending = new Map(); // id -> 合并后的最新 patch 快照
    const timers = new Map(); // id -> 防抖定时器
    const chains = new Map(); // id -> 串行链，保证同一笔记保存不并发交错

    function run(id, patch) {
      const prev = chains.get(id) || Promise.resolve();
      const task = prev.then(function () {
        return Promise.resolve()
          .then(function () { return saveFn(id, patch); })
          .then(function () { onSaved(id, patch); })
          .catch(function (e) { onFailed(id, e); });
      });
      chains.set(id, task);
      return task;
    }

    function schedule(id, patch) {
      pending.set(id, Object.assign({}, pending.get(id), patch));
      clearTimeout(timers.get(id));
      const t = setTimeout(function () {
        timers.delete(id);
        flush(id);
      }, debounceMs);
      if (t.unref) t.unref(); // 防抖定时器不应阻塞进程退出/测试收尾
      timers.set(id, t);
    }

    function flush(id) {
      clearTimeout(timers.get(id));
      timers.delete(id);
      const patch = pending.get(id);
      if (!patch) return chains.get(id) || Promise.resolve();
      pending.delete(id);
      return run(id, patch);
    }

    function flushAll() {
      return Promise.all(Array.from(pending.keys()).map(flush));
    }

    function hasPending(id) {
      return pending.has(id);
    }

    return { schedule: schedule, flush: flush, flushAll: flushAll, hasPending: hasPending };
  };
});
