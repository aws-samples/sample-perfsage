# PerfSage Frontend

A 3-step performance testing UI built with Next.js 14, TypeScript, and Tailwind CSS.

## Overview

PerfSage provides an intuitive interface for:

1. **Generate** – Upload OpenAPI specs and configure load test generation
2. **Execute** – Review generated k6 scripts and run performance tests
3. **Analyze** – View results, anomalies, and recommendations

## Getting Started

```bash
# Install dependencies
npm install

# Set up environment variables
cp .env.example .env.local
# Edit .env.local with your AWS credentials and Lambda endpoints

# Run development server
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) in your browser.

## Environment Variables

| Variable                      | Description                         |
| ----------------------------- | ----------------------------------- |
| `NEXT_PUBLIC_TESTGEN_LAMBDA`  | TestGen Lambda function name        |
| `NEXT_PUBLIC_EXECUTOR_LAMBDA` | Executor Agent Lambda function name |
| `NEXT_PUBLIC_AWS_REGION`      | AWS region (e.g., `us-west-2`)      |
| `AWS_ACCESS_KEY_ID`           | AWS access key                      |
| `AWS_SECRET_ACCESS_KEY`       | AWS secret key                      |
| `AWS_SESSION_TOKEN`           | AWS session token (optional)        |

## Tech Stack

- **Framework**: Next.js 14 (App Router)
- **Language**: TypeScript
- **Styling**: Tailwind CSS 3
- **AWS SDK**: @aws-sdk/client-lambda, @aws-sdk/client-dynamodb
- **HTTP**: Axios
- **Code Display**: react-syntax-highlighter

## Project Structure

```
src/
├── app/            → Next.js app router pages
├── components/     → React UI components
├── lib/            → API clients, types
└── hooks/          → Custom React hooks
```
