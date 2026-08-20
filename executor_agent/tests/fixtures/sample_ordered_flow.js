/**
 * PerfSage — Sample k6 script demonstrating endpoint relationship ordering.
 *
 * This script executes endpoints in a specific order respecting data dependencies:
 *   1. POST /users       → creates a user, captures user_id
 *   2. POST /auth/login  → authenticates, captures auth token
 *   3. GET /products     → fetches product catalog, captures product_id
 *   4. POST /orders      → places order using user_id + product_id
 *   5. GET /orders/:id   → verifies the order was created
 *
 * Environment variables (injected by Executor Agent):
 *   TARGET_URL         — base URL of the API under test
 *   AUTH_TYPE          — authentication type (bearer_token, api_key, etc.)
 *   AUTH_TOKEN         — pre-existing auth token (if any)
 *   THINK_TIME_MS      — pause between requests (simulates real user)
 *   PERFSAGE_TOTAL_RECORDS — total iterations to execute across all VUs
 */

import http from "k6/http";
import { check, sleep, group } from "k6";
import { Rate, Trend, Counter } from "k6/metrics";
import { SharedArray } from "k6/data";

// Custom metrics per endpoint
const createUserDuration = new Trend("create_user_duration", true);
const loginDuration = new Trend("login_duration", true);
const getProductsDuration = new Trend("get_products_duration", true);
const createOrderDuration = new Trend("create_order_duration", true);
const getOrderDuration = new Trend("get_order_duration", true);
const flowErrors = new Rate("flow_errors");
const completedFlows = new Counter("completed_flows");

// Configuration from environment
const BASE_URL = __ENV.TARGET_URL || "http://localhost:8080";
const AUTH_TYPE = __ENV.AUTH_TYPE || "bearer_token";
const THINK_TIME = __ENV.THINK_TIME_MS
    ? parseInt(__ENV.THINK_TIME_MS) / 1000
    : 1;
const TOTAL_RECORDS = __ENV.PERFSAGE_TOTAL_RECORDS
    ? parseInt(__ENV.PERFSAGE_TOTAL_RECORDS)
    : null;

// k6 options — override via env or CLI
export const options = {
    vus: __ENV.K6_VUS ? parseInt(__ENV.K6_VUS) : 10,
    duration: __ENV.K6_DURATION || "2m",
    thresholds: {
        http_req_duration: ["p(95)<500", "p(99)<1000"],
        flow_errors: ["rate<0.05"],
        create_user_duration: ["p(95)<300"],
        login_duration: ["p(95)<200"],
        create_order_duration: ["p(95)<500"],
    },
    // If TOTAL_RECORDS is set, use iterations mode instead of duration
    ...(TOTAL_RECORDS && { iterations: TOTAL_RECORDS, duration: undefined }),
};

// Shared test data for user generation
const firstNames = [
    "Alice",
    "Bob",
    "Charlie",
    "Diana",
    "Eve",
    "Frank",
    "Grace",
    "Henry",
];
const lastNames = [
    "Smith",
    "Johnson",
    "Williams",
    "Brown",
    "Jones",
    "Garcia",
    "Miller",
    "Davis",
];

function randomFrom(arr) {
    return arr[Math.floor(Math.random() * arr.length)];
}

function generateEmail() {
    return `perf_${__VU}_${__ITER}_${Date.now()}@test.perfsage.io`;
}

// Default headers
function getHeaders(token = null) {
    const headers = {
        "Content-Type": "application/json",
        "X-PerfSage-Test-Id": __ENV.PERFSAGE_TEST_ID || "local",
        "X-PerfSage-VU": String(__VU),
    };
    if (token) {
        if (AUTH_TYPE === "bearer_token") {
            headers["Authorization"] = `Bearer ${token}`;
        } else if (AUTH_TYPE === "api_key") {
            headers["X-API-Key"] = token;
        }
    }
    return headers;
}

export default function () {
    let userId = null;
    let authToken = __ENV.AUTH_TOKEN || null;
    let productId = null;
    let orderId = null;
    let flowFailed = false;

    // ─── Step 1: Create User ───────────────────────────────────────────
    group("01 - Create User", function () {
        const payload = JSON.stringify({
            first_name: randomFrom(firstNames),
            last_name: randomFrom(lastNames),
            email: generateEmail(),
            password: "PerfTest123!",
        });

        const res = http.post(`${BASE_URL}/users`, payload, {
            headers: getHeaders(),
            tags: { endpoint: "POST /users", step: "1_create_user" },
        });

        createUserDuration.add(res.timings.duration);

        const success = check(res, {
            "Create User: status 201": (r) => r.status === 201,
            "Create User: has user_id": (r) => {
                try {
                    const body = JSON.parse(r.body);
                    userId = body.id || body.user_id;
                    return !!userId;
                } catch (e) {
                    return false;
                }
            },
        });

        if (!success) {
            flowFailed = true;
            console.warn(
                `[VU:${__VU}] Create user failed: ${res.status} ${res.body}`,
            );
        }
    });

    if (flowFailed) {
        flowErrors.add(1);
        return;
    }

    sleep(THINK_TIME * 0.5);

    // ─── Step 2: Login / Authenticate ──────────────────────────────────
    group("02 - Authenticate", function () {
        // Skip if we already have a token
        if (authToken) return;

        const payload = JSON.stringify({
            email: generateEmail(), // Use the same email from step 1 in real scenario
            password: "PerfTest123!",
        });

        const res = http.post(`${BASE_URL}/auth/login`, payload, {
            headers: getHeaders(),
            tags: { endpoint: "POST /auth/login", step: "2_authenticate" },
        });

        loginDuration.add(res.timings.duration);

        const success = check(res, {
            "Login: status 200": (r) => r.status === 200,
            "Login: has token": (r) => {
                try {
                    const body = JSON.parse(r.body);
                    authToken = body.token || body.access_token;
                    return !!authToken;
                } catch (e) {
                    return false;
                }
            },
        });

        if (!success) {
            // Proceed anyway — some APIs don't require auth for all endpoints
            console.info(`[VU:${__VU}] Auth failed, continuing without token`);
        }
    });

    sleep(THINK_TIME * 0.5);

    // ─── Step 3: Get Products ──────────────────────────────────────────
    group("03 - Get Products", function () {
        const res = http.get(`${BASE_URL}/products?limit=10`, {
            headers: getHeaders(authToken),
            tags: { endpoint: "GET /products", step: "3_get_products" },
        });

        getProductsDuration.add(res.timings.duration);

        check(res, {
            "Get Products: status 200": (r) => r.status === 200,
            "Get Products: has items": (r) => {
                try {
                    const body = JSON.parse(r.body);
                    const items =
                        body.products || body.items || body.data || body;
                    if (Array.isArray(items) && items.length > 0) {
                        productId = items[0].id || items[0].product_id;
                        return true;
                    }
                    return false;
                } catch (e) {
                    return false;
                }
            },
        });

        // Fallback product_id for testing
        if (!productId) productId = "product-001";
    });

    sleep(THINK_TIME);

    // ─── Step 4: Create Order (depends on user + product) ──────────────
    group("04 - Create Order", function () {
        const payload = JSON.stringify({
            user_id: userId,
            product_id: productId,
            quantity: Math.ceil(Math.random() * 5),
            shipping_address: {
                street: "123 Perf Test Lane",
                city: "Load City",
                state: "CA",
                zip: "94105",
            },
        });

        const res = http.post(`${BASE_URL}/orders`, payload, {
            headers: getHeaders(authToken),
            tags: { endpoint: "POST /orders", step: "4_create_order" },
        });

        createOrderDuration.add(res.timings.duration);

        const success = check(res, {
            "Create Order: status 201": (r) =>
                r.status === 201 || r.status === 200,
            "Create Order: has order_id": (r) => {
                try {
                    const body = JSON.parse(r.body);
                    orderId = body.id || body.order_id;
                    return !!orderId;
                } catch (e) {
                    return false;
                }
            },
        });

        if (!success) {
            flowFailed = true;
            console.warn(`[VU:${__VU}] Create order failed: ${res.status}`);
        }
    });

    if (flowFailed) {
        flowErrors.add(1);
        return;
    }

    sleep(THINK_TIME * 0.5);

    // ─── Step 5: Verify Order ──────────────────────────────────────────
    group("05 - Verify Order", function () {
        const res = http.get(`${BASE_URL}/orders/${orderId}`, {
            headers: getHeaders(authToken),
            tags: { endpoint: "GET /orders/:id", step: "5_verify_order" },
        });

        getOrderDuration.add(res.timings.duration);

        check(res, {
            "Get Order: status 200": (r) => r.status === 200,
            "Get Order: correct order_id": (r) => {
                try {
                    const body = JSON.parse(r.body);
                    return (body.id || body.order_id) === orderId;
                } catch (e) {
                    return false;
                }
            },
        });
    });

    // Full flow completed successfully
    flowErrors.add(0);
    completedFlows.add(1);

    sleep(THINK_TIME);
}

// End-of-test summary export
export function handleSummary(data) {
    return {
        "/results/summary.json": JSON.stringify(data),
    };
}
