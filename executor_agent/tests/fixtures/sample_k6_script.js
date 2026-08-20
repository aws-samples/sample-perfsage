import http from "k6/http";
import { check, sleep } from "k6";
import { Rate, Trend } from "k6/metrics";

// Custom metrics
const errorRate = new Rate("errors");
const latencyTrend = new Trend("latency_trend");

// Test configuration (override via env vars or CLI)
export const options = {
    vus: __ENV.K6_VUS ? parseInt(__ENV.K6_VUS) : 10,
    duration: __ENV.K6_DURATION || "30s",
    thresholds: {
        http_req_duration: ["p(95)<500", "p(99)<1000"],
        errors: ["rate<0.1"],
    },
};

const BASE_URL = __ENV.TARGET_URL || "http://mock-api:8080";

export default function () {
    // GET request
    const getRes = http.get(`${BASE_URL}/get`);
    check(getRes, {
        "GET status is 200": (r) => r.status === 200,
        "GET response time < 500ms": (r) => r.timings.duration < 500,
    });
    errorRate.add(getRes.status !== 200);
    latencyTrend.add(getRes.timings.duration);

    sleep(0.5);

    // POST request
    const payload = JSON.stringify({
        user: `user_${__VU}`,
        timestamp: new Date().toISOString(),
        data: { key: "value", iteration: __ITER },
    });

    const postRes = http.post(`${BASE_URL}/post`, payload, {
        headers: { "Content-Type": "application/json" },
    });
    check(postRes, {
        "POST status is 200": (r) => r.status === 200,
    });
    errorRate.add(postRes.status !== 200);
    latencyTrend.add(postRes.timings.duration);

    sleep(0.5);
}

export function handleSummary(data) {
    return {
        "/results/summary.json": JSON.stringify(data),
    };
}
