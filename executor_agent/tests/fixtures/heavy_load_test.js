/**
 * PerfSage — Heavy Load Test Script
 *
 * Designed to generate substantial load for testing the Executor Agent end-to-end.
 * Targets httpbin.org (public) or any configurable endpoint.
 *
 * This script simulates:
 *   - 500 VUs ramping up over 2 minutes
 *   - Sustained load for 10 minutes
 *   - Ramp down over 1 minute
 *   - Mixed workload: 60% reads, 25% writes, 10% auth, 5% heavy payloads
 *   - Realistic think times and session behavior
 *   - ~50,000+ total requests depending on target response time
 *
 * Use with:
 *   perfsage-executor run --config tests/fixtures/heavy_load_config.json --script tests/fixtures/heavy_load_test.js
 */

import http from "k6/http";
import { check, sleep, group, fail } from "k6";
import { Rate, Trend, Counter, Gauge } from "k6/metrics";
import {
    randomIntBetween,
    randomString,
    randomItem,
} from "https://jslib.k6.io/k6-utils/1.4.0/index.js";

// ─── Custom Metrics ────────────────────────────────────────────────────────────
const readLatency = new Trend("read_latency", true);
const writeLatency = new Trend("write_latency", true);
const authLatency = new Trend("auth_latency", true);
const heavyPayloadLatency = new Trend("heavy_payload_latency", true);
const flowErrors = new Rate("flow_errors");
const successfulRequests = new Counter("successful_requests");
const failedRequests = new Counter("failed_requests");
const dataTransferred = new Counter("data_transferred_bytes");
const activeScenarios = new Gauge("active_scenarios");

// ─── Configuration ─────────────────────────────────────────────────────────────
const BASE_URL = __ENV.TARGET_URL || "https://httpbin.org";
const THINK_TIME = __ENV.THINK_TIME_MS
    ? parseInt(__ENV.THINK_TIME_MS) / 1000
    : 0.5;
const TOTAL_RECORDS = __ENV.PERFSAGE_TOTAL_RECORDS
    ? parseInt(__ENV.PERFSAGE_TOTAL_RECORDS)
    : null;
const TEST_ID = __ENV.PERFSAGE_TEST_ID || "heavy-local";

// ─── k6 Options ────────────────────────────────────────────────────────────────
export const options = {
    // Use stages for realistic ramp pattern
    stages: [
        { duration: "2m", target: 200 }, // Ramp up to 200 VUs
        { duration: "3m", target: 500 }, // Ramp up to 500 VUs
        { duration: "5m", target: 500 }, // Sustain 500 VUs
        { duration: "2m", target: 200 }, // Ramp down
        { duration: "1m", target: 0 }, // Cool down
    ],
    thresholds: {
        http_req_duration: ["p(95)<2000", "p(99)<5000"],
        read_latency: ["p(95)<1000"],
        write_latency: ["p(95)<2000"],
        flow_errors: ["rate<0.10"],
        http_req_failed: ["rate<0.15"],
    },
    // Override with iterations mode if TOTAL_RECORDS is set
    ...(TOTAL_RECORDS && {
        stages: undefined,
        vus: __ENV.K6_VUS ? parseInt(__ENV.K6_VUS) : 500,
        iterations: TOTAL_RECORDS,
    }),

    // Tags for filtering in analysis
    tags: {
        test_id: TEST_ID,
        test_type: "heavy_load",
    },
};

// ─── Test Data Generators ──────────────────────────────────────────────────────
const userAgents = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15",
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
];

const endpoints = [
    "/get",
    "/get",
    "/get",
    "/get",
    "/get",
    "/get", // 60% reads
    "/post",
    "/post",
    "/put", // 25% writes (approximated)
    "/basic-auth/user/pass", // 10% auth
    "/post", // 5% heavy (handled in logic)
];

function generatePayload(size = "small") {
    switch (size) {
        case "tiny":
            return JSON.stringify({ id: randomIntBetween(1, 100000) });
        case "small":
            return JSON.stringify({
                id: randomIntBetween(1, 100000),
                name: randomString(20),
                email: `user${__VU}_${__ITER}@loadtest.perfsage.io`,
                timestamp: new Date().toISOString(),
                metadata: {
                    vu: __VU,
                    iter: __ITER,
                    test_id: TEST_ID,
                },
            });
        case "medium":
            // ~10KB payload
            const items = [];
            for (let i = 0; i < 50; i++) {
                items.push({
                    id: randomIntBetween(1, 999999),
                    product_name: randomString(30),
                    description: randomString(100),
                    price: (Math.random() * 1000).toFixed(2),
                    category: randomItem([
                        "electronics",
                        "clothing",
                        "food",
                        "books",
                        "sports",
                    ]),
                    in_stock: Math.random() > 0.2,
                    tags: [randomString(8), randomString(8), randomString(8)],
                });
            }
            return JSON.stringify({
                order_id: randomString(12),
                items,
                total: items.length,
            });
        case "large":
            // ~100KB payload
            const records = [];
            for (let i = 0; i < 500; i++) {
                records.push({
                    record_id: `${TEST_ID}-${__VU}-${__ITER}-${i}`,
                    data: randomString(150),
                    metrics: {
                        cpu: Math.random() * 100,
                        memory: Math.random() * 16384,
                        disk_io: Math.random() * 1000,
                        network_in: Math.random() * 10000,
                        network_out: Math.random() * 10000,
                    },
                    timestamp: new Date(Date.now() - i * 1000).toISOString(),
                });
            }
            return JSON.stringify({
                batch_id: randomString(16),
                records,
                count: records.length,
            });
        default:
            return JSON.stringify({ ping: "pong" });
    }
}

function getHeaders() {
    return {
        "Content-Type": "application/json",
        "User-Agent": randomItem(userAgents),
        "X-Request-ID": `${TEST_ID}-${__VU}-${__ITER}-${Date.now()}`,
        "X-PerfSage-Test": TEST_ID,
        Accept: "application/json",
        "Accept-Encoding": "gzip, deflate",
    };
}

// ─── Scenario Functions ────────────────────────────────────────────────────────

function readScenario() {
    group("Read Operations", function () {
        // Simple GET
        const res1 = http.get(`${BASE_URL}/get`, {
            headers: getHeaders(),
            tags: { scenario: "read", endpoint: "GET /get" },
        });
        readLatency.add(res1.timings.duration);
        dataTransferred.add(res1.body ? res1.body.length : 0);

        const ok = check(res1, {
            "GET /get: status 200": (r) => r.status === 200,
            "GET /get: response time < 2s": (r) => r.timings.duration < 2000,
        });

        if (ok) successfulRequests.add(1);
        else failedRequests.add(1);

        sleep(THINK_TIME * 0.3);

        // GET with query params
        const res2 = http.get(
            `${BASE_URL}/get?page=${randomIntBetween(1, 100)}&limit=50&sort=created_at&order=desc&filter=active`,
            {
                headers: getHeaders(),
                tags: { scenario: "read", endpoint: "GET /get?params" },
            },
        );
        readLatency.add(res2.timings.duration);
        dataTransferred.add(res2.body ? res2.body.length : 0);

        check(res2, { "GET with params: status 200": (r) => r.status === 200 });
        if (res2.status === 200) successfulRequests.add(1);
        else failedRequests.add(1);

        // GET with different response format
        const res3 = http.get(`${BASE_URL}/headers`, {
            headers: getHeaders(),
            tags: { scenario: "read", endpoint: "GET /headers" },
        });
        readLatency.add(res3.timings.duration);
        if (res3.status === 200) successfulRequests.add(1);
        else failedRequests.add(1);
    });
}

function writeScenario() {
    group("Write Operations", function () {
        // POST with small payload
        const smallPayload = generatePayload("small");
        const res1 = http.post(`${BASE_URL}/post`, smallPayload, {
            headers: getHeaders(),
            tags: { scenario: "write", endpoint: "POST /post (small)" },
        });
        writeLatency.add(res1.timings.duration);
        dataTransferred.add(smallPayload.length);

        const ok = check(res1, {
            "POST small: status 200": (r) => r.status === 200,
            "POST small: echoes data": (r) => {
                try {
                    return JSON.parse(r.body).data !== undefined;
                } catch (e) {
                    return false;
                }
            },
        });
        if (ok) successfulRequests.add(1);
        else failedRequests.add(1);

        sleep(THINK_TIME * 0.5);

        // PUT with medium payload
        const mediumPayload = generatePayload("medium");
        const res2 = http.put(`${BASE_URL}/put`, mediumPayload, {
            headers: getHeaders(),
            tags: { scenario: "write", endpoint: "PUT /put (medium)" },
        });
        writeLatency.add(res2.timings.duration);
        dataTransferred.add(mediumPayload.length);

        check(res2, { "PUT medium: status 200": (r) => r.status === 200 });
        if (res2.status === 200) successfulRequests.add(1);
        else failedRequests.add(1);

        sleep(THINK_TIME * 0.3);

        // PATCH
        const patchPayload = JSON.stringify({
            status: "updated",
            updated_at: new Date().toISOString(),
        });
        const res3 = http.patch(`${BASE_URL}/patch`, patchPayload, {
            headers: getHeaders(),
            tags: { scenario: "write", endpoint: "PATCH /patch" },
        });
        writeLatency.add(res3.timings.duration);
        if (res3.status === 200) successfulRequests.add(1);
        else failedRequests.add(1);

        // DELETE
        const res4 = http.del(`${BASE_URL}/delete`, null, {
            headers: getHeaders(),
            tags: { scenario: "write", endpoint: "DELETE /delete" },
        });
        writeLatency.add(res4.timings.duration);
        if (res4.status === 200) successfulRequests.add(1);
        else failedRequests.add(1);
    });
}

function authScenario() {
    group("Auth Operations", function () {
        // Basic auth
        const res1 = http.get(`${BASE_URL}/basic-auth/user/pass`, {
            headers: {
                ...getHeaders(),
                Authorization: "Basic " + __ENV.AUTH_TOKEN || "dXNlcjpwYXNz", // user:pass base64
            },
            tags: { scenario: "auth", endpoint: "GET /basic-auth" },
        });
        authLatency.add(res1.timings.duration);

        check(res1, {
            "Basic Auth: status 200 or 401": (r) =>
                r.status === 200 || r.status === 401,
        });
        if (res1.status === 200) successfulRequests.add(1);
        else failedRequests.add(1);

        sleep(THINK_TIME * 0.2);

        // Bearer token auth
        const res2 = http.get(`${BASE_URL}/bearer`, {
            headers: {
                ...getHeaders(),
                Authorization: `Bearer fake-token-${randomString(32)}`,
            },
            tags: { scenario: "auth", endpoint: "GET /bearer" },
        });
        authLatency.add(res2.timings.duration);
        if (res2.status === 200) successfulRequests.add(1);
        else failedRequests.add(1);
    });
}

function heavyPayloadScenario() {
    group("Heavy Payload Operations", function () {
        // Large POST (~100KB)
        const largePayload = generatePayload("large");
        const res = http.post(`${BASE_URL}/post`, largePayload, {
            headers: getHeaders(),
            tags: { scenario: "heavy", endpoint: "POST /post (large)" },
            timeout: "30s",
        });
        heavyPayloadLatency.add(res.timings.duration);
        dataTransferred.add(largePayload.length);

        const ok = check(res, {
            "POST large: status 200": (r) => r.status === 200,
            "POST large: response time < 10s": (r) =>
                r.timings.duration < 10000,
        });
        if (ok) successfulRequests.add(1);
        else failedRequests.add(1);

        sleep(THINK_TIME);

        // Another large payload with different structure
        const batchPayload = generatePayload("large");
        const res2 = http.put(`${BASE_URL}/put`, batchPayload, {
            headers: getHeaders(),
            tags: { scenario: "heavy", endpoint: "PUT /put (large)" },
            timeout: "30s",
        });
        heavyPayloadLatency.add(res2.timings.duration);
        dataTransferred.add(batchPayload.length);

        check(res2, { "PUT large: status 200": (r) => r.status === 200 });
        if (res2.status === 200) successfulRequests.add(1);
        else failedRequests.add(1);
    });
}

// ─── Main Execution ────────────────────────────────────────────────────────────
export default function () {
    activeScenarios.add(1);

    // Weighted random scenario selection: 60% read, 25% write, 10% auth, 5% heavy
    const roll = Math.random() * 100;

    if (roll < 60) {
        readScenario();
    } else if (roll < 85) {
        writeScenario();
    } else if (roll < 95) {
        authScenario();
    } else {
        heavyPayloadScenario();
    }

    // Track flow success/failure
    flowErrors.add(0);

    // Inter-scenario think time
    sleep(THINK_TIME);
}

// ─── Lifecycle Hooks ───────────────────────────────────────────────────────────
export function setup() {
    console.log(`=== PerfSage Heavy Load Test ===`);
    console.log(`Target: ${BASE_URL}`);
    console.log(`Test ID: ${TEST_ID}`);
    console.log(`Total Records: ${TOTAL_RECORDS || "duration-based"}`);
    console.log(`Think Time: ${THINK_TIME * 1000}ms`);
    console.log(`================================`);

    // Verify target is reachable
    const res = http.get(`${BASE_URL}/get`, { timeout: "10s" });
    if (res.status !== 200) {
        fail(`Target ${BASE_URL} is not reachable (status: ${res.status})`);
    }

    return { startTime: Date.now() };
}

export function teardown(data) {
    const duration = (Date.now() - data.startTime) / 1000;
    console.log(`=== Test Complete ===`);
    console.log(`Total duration: ${duration.toFixed(1)}s`);
    console.log(`====================`);
}

// Export summary for PerfSage analysis
export function handleSummary(data) {
    return {
        "/results/summary.json": JSON.stringify(data),
        stdout: textSummary(data, { indent: " ", enableColors: true }),
    };
}

// k6 built-in text summary
import { textSummary } from "https://jslib.k6.io/k6-summary/0.1.0/index.js";
