"use strict";

const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const https = require("node:https");
const { requestAddress } = require("../windows_dns_https");

function fakeRequest(onResponse, action) {
  const request = new EventEmitter();
  request.setTimeout = () => request;
  request.write = () => true;
  request.destroy = (error) => {
    if (error) setImmediate(() => request.emit("error", error));
  };
  request.end = () => setImmediate(() => action(request, onResponse));
  return request;
}

async function main() {
  const original = https.request;
  const target = new URL("https://qieman.com/pmdj/v1/test");
  try {
    https.request = (_options, onResponse) => fakeRequest(onResponse, (request) => request.emit("close"));
    await assert.rejects(
      requestAddress(target, "127.0.0.1", {}, 100, 1024),
      /closed before response completion/,
    );

    https.request = (_options, onResponse) => fakeRequest(onResponse, (_request, callback) => {
      const response = new EventEmitter();
      response.statusCode = 200;
      response.headers = {};
      callback(response);
      response.emit("aborted");
    });
    await assert.rejects(
      requestAddress(target, "127.0.0.1", {}, 100, 1024),
      /aborted before completion/,
    );

    https.request = (_options, onResponse) => fakeRequest(onResponse, (request, callback) => {
      const response = new EventEmitter();
      response.statusCode = 200;
      response.headers = { "content-type": "application/json" };
      callback(response);
      response.emit("data", Buffer.from('{"ok":true}'));
      response.emit("end");
      request.emit("close");
    });
    const response = await requestAddress(target, "127.0.0.1", {}, 100, 1024);
    assert.equal(response.status, 200);
    assert.equal(response.text, '{"ok":true}');

    https.request = (_options, onResponse) => fakeRequest(onResponse, () => {});
    await assert.rejects(
      requestAddress(target, "127.0.0.1", {}, 100, 1024, 30),
      (error) => error.code === "HTTPS_TOTAL_TIMEOUT" && /total timeout/.test(error.message),
    );
  } finally {
    https.request = original;
  }
  process.stdout.write("windows DNS HTTPS settlement tests: 4 passed\n");
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
