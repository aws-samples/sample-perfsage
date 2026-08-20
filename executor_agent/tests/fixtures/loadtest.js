import http from 'k6/http';
import { check, sleep, group } from 'k6';
import { Rate } from 'k6/metrics';
import { randomIntBetween } from 'https://jslib.k6.io/k6-utils/1.2.0/index.js';

// ---------------------------------------------------------------------------
// Base URL — override via: k6 run -e BASE_URL=https://your-server.com/v2
// ---------------------------------------------------------------------------
const BASE_URL = __ENV.BASE_URL || 'https://hr-api.example.com/v2';

// ---------------------------------------------------------------------------
// Custom metrics
// ---------------------------------------------------------------------------
const duplicateRejected  = new Rate('duplicate_rejected');
const invalidRefRejected = new Rate('invalid_ref_rejected');

// ---------------------------------------------------------------------------
// Realistic static data (built once in init context)
// ---------------------------------------------------------------------------
const COMPANY_NAMES = [
  'Acme Corporation', 'GlobalTech Industries', 'Nexus Solutions',
  'Pinnacle Enterprises', 'Vertex Systems', 'Horizon Analytics',
  'Catalyst Group', 'Meridian Holdings', 'Apex Dynamics', 'Stratos Global',
];
const INDUSTRIES = ['technology', 'finance', 'healthcare', 'manufacturing', 'retail'];

const DEPT_NAMES = [
  'Engineering', 'Sales', 'Marketing', 'Finance', 'Human Resources',
  'Operations', 'Product Management', 'Legal', 'Customer Success',
  'Research & Development', 'IT Infrastructure', 'Business Development',
];

const ROLES = ['engineer', 'manager', 'analyst', 'designer', 'hr', 'finance', 'operations'];

const FIRST_NAMES = [
  'James', 'Sarah', 'Michael', 'Emily', 'David', 'Jessica', 'Robert',
  'Ashley', 'William', 'Amanda', 'Christopher', 'Stephanie', 'Daniel',
  'Nicole', 'Matthew', 'Lauren', 'Andrew', 'Megan', 'Joshua', 'Rachel',
];
const LAST_NAMES = [
  'Smith', 'Johnson', 'Williams', 'Brown', 'Jones', 'Garcia', 'Miller',
  'Davis', 'Wilson', 'Taylor', 'Anderson', 'Thomas', 'Jackson', 'White',
  'Harris', 'Martin', 'Thompson', 'Martinez', 'Robinson', 'Clark',
];

const ADDRESSES = [
  '100 Innovation Drive, San Francisco, CA 94105',
  '200 Enterprise Blvd, New York, NY 10001',
  '300 Corporate Ave, Chicago, IL 60601',
  '400 Business Park, Austin, TX 78701',
  '500 Tech Campus, Seattle, WA 98101',
];

// Seed counts
const NUM_COMPANIES  = 10;
const NUM_DEPTS      = 50;
const NUM_EMPLOYEES  = 500;

// JSON headers
const JSON_PARAMS = { headers: { 'Content-Type': 'application/json' } };

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function pick(arr) {
  return arr[Math.floor(Math.random() * arr.length)];
}

function uniqueEmail(firstName, lastName, idx) {
  return `${firstName.toLowerCase()}.${lastName.toLowerCase()}.${idx}@corp.example.com`;
}

function randomSalary() {
  return randomIntBetween(55000, 220000);
}

function randomBudget() {
  return randomIntBetween(500000, 10000000);
}

function randomDate() {
  const year  = randomIntBetween(2015, 2024);
  const month = String(randomIntBetween(1, 12)).padStart(2, '0');
  const day   = String(randomIntBetween(1, 28)).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

// ---------------------------------------------------------------------------
// k6 options — stress test: ramp 0→100 over 3 min, hold 50 for 5 min, ramp down
// ---------------------------------------------------------------------------
export const options = {
  stages: [
    { duration: '1m',  target: 25  },  // warm-up
    { duration: '2m',  target: 100 },  // stress ramp to peak
    { duration: '5m',  target: 50  },  // sustained load at 50 VUs
    { duration: '1m',  target: 0   },  // ramp down
  ],
  thresholds: {
    // Happy-path requests must stay healthy
    'http_req_failed{test_type:happy_path}':   ['rate<0.02'],
    'http_req_duration{test_type:happy_path}': ['p(95)<2000'],
    // Edge-case requests intentionally produce 4xx — disable strict failure threshold
    'http_req_failed{test_type:edge_case}':    ['rate<1.1'],
    // Custom correctness metrics
    'duplicate_rejected':   ['rate>0.95'],
    'invalid_ref_rejected': ['rate>0.95'],
  },
};

// ---------------------------------------------------------------------------
// setup() — seed data: companies → departments → employees
// ---------------------------------------------------------------------------
export function setup() {
  // ── 1. Create companies ──────────────────────────────────────────────────
  const companyIds = [];
  for (let i = 0; i < NUM_COMPANIES; i++) {
    const payload = JSON.stringify({
      name:           COMPANY_NAMES[i % COMPANY_NAMES.length],
      industry:       INDUSTRIES[i % INDUSTRIES.length],
      address:        ADDRESSES[i % ADDRESSES.length],
      employee_count: randomIntBetween(200, 5000),
    });
    const res = http.post(`${BASE_URL}/companies`, payload, JSON_PARAMS);
    if (res.status === 201 || res.status === 200) {
      const id = res.json('id');
      if (id) companyIds.push(id);
    }
  }
  if (companyIds.length === 0) {
    console.error('setup: no companies created — aborting seed');
    return { companyIds: [], deptIds: [], employeeIds: [] };
  }

  // ── 2. Create departments (round-robin across companies) ─────────────────
  const deptIds = [];
  for (let i = 0; i < NUM_DEPTS; i++) {
    const payload = JSON.stringify({
      name:       `${DEPT_NAMES[i % DEPT_NAMES.length]} ${Math.ceil((i + 1) / DEPT_NAMES.length)}`,
      company_id: companyIds[i % companyIds.length],
      budget:     randomBudget(),
      head_count: randomIntBetween(5, 150),
    });
    const res = http.post(`${BASE_URL}/departments`, payload, JSON_PARAMS);
    if (res.status === 201 || res.status === 200) {
      const id = res.json('id');
      if (id) deptIds.push(id);
    }
  }
  if (deptIds.length === 0) {
    console.error('setup: no departments created — aborting employee seed');
    return { companyIds, deptIds: [], employeeIds: [] };
  }

  // ── 3. Create employees (round-robin across departments) ─────────────────
  const employeeIds = [];
  for (let i = 0; i < NUM_EMPLOYEES; i++) {
    const firstName = FIRST_NAMES[i % FIRST_NAMES.length];
    const lastName  = LAST_NAMES[Math.floor(i / FIRST_NAMES.length) % LAST_NAMES.length];
    const payload = JSON.stringify({
      name:          `${firstName} ${lastName}`,
      email:         uniqueEmail(firstName, lastName, i),
      department_id: deptIds[i % deptIds.length],
      role:          ROLES[i % ROLES.length],
      salary:        randomSalary(),
      hire_date:     randomDate(),
    });
    const res = http.post(`${BASE_URL}/employees`, payload, JSON_PARAMS);
    if (res.status === 201 || res.status === 200) {
      const id = res.json('id');
      if (id) employeeIds.push(id);
    }
  }

  console.log(`setup complete — companies:${companyIds.length} depts:${deptIds.length} employees:${employeeIds.length}`);
  return { companyIds, deptIds, employeeIds };
}

// ---------------------------------------------------------------------------
// default function — main VU loop
// ---------------------------------------------------------------------------
export default function (data) {
  const { companyIds, deptIds, employeeIds } = data;

  // Guard: if setup produced no IDs, skip iteration
  if (!companyIds || companyIds.length === 0) {
    sleep(1);
    return;
  }

  const roll = Math.random();

  // ── Weighted traffic distribution ────────────────────────────────────────
  // 60% reads, 15% company ops, 10% dept ops, 10% employee ops, 5% edge cases
  if (roll < 0.60) {
    // ── READ-HEAVY BLOCK ──────────────────────────────────────────────────
    const readRoll = Math.random();

    if (readRoll < 0.33) {
      // List companies with pagination
      group('list_companies', function () {
        const limit  = randomIntBetween(10, 50);
        const offset = randomIntBetween(0, 5) * limit;
        const res = http.get(
          `${BASE_URL}/companies?limit=${limit}&offset=${offset}`,
          { tags: { endpoint: 'listCompanies', test_type: 'happy_path' } },
        );
        check(res, {
          'listCompanies status 200': (r) => r.status === 200,
        });
      });

    } else if (readRoll < 0.66) {
      // Get single company
      group('get_company', function () {
        const companyId = pick(companyIds);
        const res = http.get(
          `${BASE_URL}/companies/${companyId}`,
          { tags: { endpoint: 'getCompany', test_type: 'happy_path' } },
        );
        check(res, {
          'getCompany status 200': (r) => r.status === 200,
        });
      });

    } else if (readRoll < 0.80) {
      // List departments filtered by company
      group('list_departments', function () {
        const companyId = pick(companyIds);
        const res = http.get(
          `${BASE_URL}/departments?company_id=${companyId}`,
          { tags: { endpoint: 'listDepartments', test_type: 'happy_path' } },
        );
        check(res, {
          'listDepartments status 200': (r) => r.status === 200,
        });
      });

    } else if (readRoll < 0.90) {
      // Get single department
      group('get_department', function () {
        const deptId = pick(deptIds);
        const res = http.get(
          `${BASE_URL}/departments/${deptId}`,
          { tags: { endpoint: 'getDepartment', test_type: 'happy_path' } },
        );
        check(res, {
          'getDepartment status 200': (r) => r.status === 200,
        });
      });

    } else {
      // List employees by department
      group('list_employees', function () {
        const deptId = pick(deptIds);
        const res = http.get(
          `${BASE_URL}/employees?department_id=${deptId}`,
          { tags: { endpoint: 'listEmployees', test_type: 'happy_path' } },
        );
        check(res, {
          'listEmployees status 200': (r) => r.status === 200,
        });
      });
    }

  } else if (roll < 0.75) {
    // ── COMPANY CRUD ──────────────────────────────────────────────────────
    const companyId = pick(companyIds);
    group('crud_company', function () {
      const updateRes = http.put(
        `${BASE_URL}/companies/${companyId}`,
        JSON.stringify({
          name:     pick(COMPANY_NAMES) + ' Updated',
          industry: pick(INDUSTRIES),
          address:  pick(ADDRESSES),
        }),
        Object.assign({}, JSON_PARAMS, { tags: { endpoint: 'updateCompany', test_type: 'happy_path' } }),
      );
      check(updateRes, {
        'updateCompany status 200': (r) => r.status === 200,
      });
    });

  } else if (roll < 0.85) {
    // ── DEPARTMENT CRUD ───────────────────────────────────────────────────
    const deptId    = pick(deptIds);
    const companyId = pick(companyIds);
    group('crud_department', function () {
      // Create a new department
      const createRes = http.post(
        `${BASE_URL}/departments`,
        JSON.stringify({
          name:       `${pick(DEPT_NAMES)} Stress-${randomIntBetween(1000, 9999)}`,
          company_id: companyId,
          budget:     randomBudget(),
          head_count: randomIntBetween(5, 100),
        }),
        Object.assign({}, JSON_PARAMS, { tags: { endpoint: 'createDepartment', test_type: 'happy_path' } }),
      );
      check(createRes, {
        'createDepartment status 201': (r) => r.status === 201 || r.status === 200,
      });

      // Update existing department
      const updateRes = http.put(
        `${BASE_URL}/departments/${deptId}`,
        JSON.stringify({
          name:   `${pick(DEPT_NAMES)} Revised`,
          budget: randomBudget(),
        }),
        Object.assign({}, JSON_PARAMS, { tags: { endpoint: 'updateDepartment', test_type: 'happy_path' } }),
      );
      check(updateRes, {
        'updateDepartment status 200': (r) => r.status === 200,
      });
    });

  } else if (roll < 0.95) {
    // ── EMPLOYEE CRUD ─────────────────────────────────────────────────────
    const deptId = pick(deptIds);
    group('crud_employee', function () {
      // Create a new employee
      const idx       = randomIntBetween(10000, 99999);
      const firstName = pick(FIRST_NAMES);
      const lastName  = pick(LAST_NAMES);
      const createRes = http.post(
        `${BASE_URL}/employees`,
        JSON.stringify({
          name:          `${firstName} ${lastName}`,
          email:         `${firstName.toLowerCase()}.${lastName.toLowerCase()}.${idx}@corp.example.com`,
          department_id: deptId,
          role:          pick(ROLES),
          salary:        randomSalary(),
          hire_date:     randomDate(),
        }),
        Object.assign({}, JSON_PARAMS, { tags: { endpoint: 'createEmployee', test_type: 'happy_path' } }),
      );
      check(createRes, {
        'createEmployee status 201': (r) => r.status === 201 || r.status === 200,
      });

      // Update an existing employee
      if (employeeIds && employeeIds.length > 0) {
        const empId     = pick(employeeIds);
        const updateRes = http.put(
          `${BASE_URL}/employees/${empId}`,
          JSON.stringify({
            role:   pick(ROLES),
            salary: randomSalary(),
          }),
          Object.assign({}, JSON_PARAMS, { tags: { endpoint: 'updateEmployee', test_type: 'happy_path' } }),
        );
        check(updateRes, {
          'updateEmployee status 200': (r) => r.status === 200,
        });
      }
    });

  } else {
    // ── EDGE CASES (≈5% of traffic) ───────────────────────────────────────
    const edgeRoll = Math.random();

    if (edgeRoll < 0.50) {
      // Edge case 1: Duplicate company name
      group('edge_duplicate_company', function () {
        const dupName = pick(COMPANY_NAMES); // already exists from setup
        const res = http.post(
          `${BASE_URL}/companies`,
          JSON.stringify({
            name:     dupName,
            industry: pick(INDUSTRIES),
            address:  pick(ADDRESSES),
          }),
          Object.assign({}, JSON_PARAMS, {
            tags: { endpoint: 'createCompany_dup', test_type: 'edge_case', edge_case: 'duplicate_name' },
          }),
        );
        // Server should reject with 409 Conflict or 422 Unprocessable Entity
        const rejected = res.status === 409 || res.status === 422 || res.status === 400;
        duplicateRejected.add(rejected);
        check(res, {
          'duplicate company rejected (409/422/400)': () => rejected,
        });
      });

    } else if (edgeRoll < 0.80) {
      // Edge case 2: Duplicate department name within same company
      group('edge_duplicate_department', function () {
        const companyId = pick(companyIds);
        const dupName   = pick(DEPT_NAMES); // likely already exists
        const res = http.post(
          `${BASE_URL}/departments`,
          JSON.stringify({
            name:       dupName,
            company_id: companyId,
            budget:     randomBudget(),
          }),
          Object.assign({}, JSON_PARAMS, {
            tags: { endpoint: 'createDepartment_dup', test_type: 'edge_case', edge_case: 'duplicate_name' },
          }),
        );
        const rejected = res.status === 409 || res.status === 422 || res.status === 400;
        duplicateRejected.add(rejected);
        check(res, {
          'duplicate department rejected (409/422/400)': () => rejected,
        });
      });

    } else {
      // Edge case 3: Employee with invalid (non-existent) department_id
      group('edge_invalid_dept_ref', function () {
        const invalidDeptId = 999999999; // guaranteed non-existent
        const idx           = randomIntBetween(100000, 999999);
        const res = http.post(
          `${BASE_URL}/employees`,
          JSON.stringify({
            name:          `Ghost Employee ${idx}`,
            email:         `ghost.${idx}@invalid.example.com`,
            department_id: invalidDeptId,
            role:          pick(ROLES),
            salary:        randomSalary(),
          }),
          Object.assign({}, JSON_PARAMS, {
            tags: { endpoint: 'createEmployee_badRef', test_type: 'edge_case', edge_case: 'invalid_reference' },
          }),
        );
        // Server should reject with 404 (dept not found) or 422 (validation error)
        const rejected = res.status === 404 || res.status === 422 || res.status === 400;
        invalidRefRejected.add(rejected);
        check(res, {
          'invalid dept ref rejected (404/422/400)': () => rejected,
        });
      });
    }
  }

  sleep(randomIntBetween(1, 3));
}

// ---------------------------------------------------------------------------
// teardown() — clean up in reverse order: employees → departments → companies
// ---------------------------------------------------------------------------
export function teardown(data) {
  const { companyIds, deptIds, employeeIds } = data;

  // Delete employees first
  if (employeeIds && employeeIds.length > 0) {
    for (const empId of employeeIds) {
      http.del(`${BASE_URL}/employees/${empId}`, null, JSON_PARAMS);
    }
  }

  // Delete departments
  if (deptIds && deptIds.length > 0) {
    for (const deptId of deptIds) {
      http.del(`${BASE_URL}/departments/${deptId}`, null, JSON_PARAMS);
    }
  }

  // Delete companies
  if (companyIds && companyIds.length > 0) {
    for (const companyId of companyIds) {
      http.del(`${BASE_URL}/companies/${companyId}`, null, JSON_PARAMS);
    }
  }

  console.log('teardown complete — all seeded resources deleted');
}