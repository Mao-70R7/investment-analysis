"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const {
  collectStrategy,
  fetchPaged,
  resultFromFile,
  runMainWithKeepAlive,
} = require("../collect_qieman_signed_history_catalog");

async function testKeepAlive() {
  const result = await runMainWithKeepAlive(() => new Promise((resolve) => {
    const pendingWithoutOwnEventLoopReference = setTimeout(() => resolve("completed"), 25);
    pendingWithoutOwnEventLoopReference.unref();
  }), (error) => { throw error; });
  assert.equal(result, "completed");
}

async function testConfiguredPageSizeControlsPagingBoundary() {
  const calls = [];
  const fetcher = {
    request: async (url) => {
      calls.push(url);
      const page = Number(new URL(url).searchParams.get("page"));
      const content = page === 0
        ? Array.from({ length: 25 }, (_, index) => ({ id: index }))
        : [{ id: 25 }];
      return {
        status: 200,
        bodyBytes: 10,
        payload: {
          content,
          totalPages: 2,
          totalElements: 26,
          last: page === 1,
        },
        parseError: null,
      };
    },
  };
  const result = await fetchPaged(
    fetcher,
    (page) => `https://qieman.com/test?page=${page}&size=25`,
    25,
  );
  assert.equal(result.complete, true);
  assert.equal(result.content.length, 26);
  assert.equal(calls.length, 2);
}

async function testHistoryTimeoutRetainsOnlyCompleteBaseline() {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "qieman-history-test-"));
  try {
    const baselineRunDir = path.join(temporary, "baseline");
    const runDir = path.join(temporary, "current");
    const baselineNavDir = path.join(baselineRunDir, "raw", "nav");
    const baselineHistoryDir = path.join(baselineRunDir, "raw", "signal_adjustments");
    fs.mkdirSync(baselineNavDir, { recursive: true });
    fs.mkdirSync(baselineHistoryDir, { recursive: true });
    fs.writeFileSync(
      path.join(baselineNavDir, "SI_TEST.json"),
      JSON.stringify([{ navDate: Date.parse("2026-08-10T00:00:00+08:00"), nav: 1.01 }]),
    );
    fs.writeFileSync(
      path.join(baselineHistoryDir, "SI_TEST.json"),
      JSON.stringify({ complete: true, content: [{ id: "old-history", adjustedOn: "2026-08-01" }] }),
    );
    let requestCount = 0;
    const fetcher = {
      lockDir: path.join(temporary, "locks"),
      request: async () => {
        requestCount += 1;
        if (requestCount === 1) {
          return {
            status: 200,
            bodyBytes: 10,
            payload: [{ navDate: Date.parse("2026-08-11T00:00:00+08:00"), nav: 1.02 }],
            parseError: null,
          };
        }
        const error = new Error("simulated long-response timeout");
        error.code = "HTTPS_TOTAL_TIMEOUT";
        throw error;
      },
    };
    const result = await collectStrategy({
      code: "SI_TEST",
      name: "test",
      fetcher,
      runDir,
      endDate: "2026-08-12",
      baselineRunDir,
      overlapDays: 5,
      signalPageSize: 25,
      regularPageSize: 100,
    });
    const retained = JSON.parse(
      fs.readFileSync(path.join(runDir, "raw", "signal_adjustments", "SI_TEST.json"), "utf8"),
    );
    assert.equal(result.complete, true);
    assert.equal(result.nav.latestDate, "2026-08-11");
    assert.equal(result.history.retainedBaseline, true);
    assert.equal(result.history.refreshComplete, false);
    assert.equal(retained.complete, true);
    assert.equal(retained.refreshComplete, false);
    assert.equal(retained.content[0].id, "old-history");
  } finally {
    fs.rmSync(temporary, { recursive: true, force: true });
  }
}

function testIncompleteHistoryFileIsRetried() {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "qieman-incomplete-test-"));
  try {
    const incomplete = path.join(temporary, "SI_TEST.json");
    fs.writeFileSync(incomplete, JSON.stringify({ complete: false, content: [{ id: "partial" }] }));
    assert.equal(resultFromFile(incomplete, "signal_adjustments"), null);
  } finally {
    fs.rmSync(temporary, { recursive: true, force: true });
  }
}

async function main() {
  await testKeepAlive();
  await testConfiguredPageSizeControlsPagingBoundary();
  await testHistoryTimeoutRetainsOnlyCompleteBaseline();
  testIncompleteHistoryFileIsRetried();
  process.stdout.write("qieman history collector tests: 4 passed\n");
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
