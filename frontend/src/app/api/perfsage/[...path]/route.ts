/**
 * Server-side proxy for PerfSage API Gateway.
 *
 * This avoids CORS issues by making AWS SigV4-signed requests from the server,
 * not the browser. The frontend calls /api/perfsage/jobs, /api/perfsage/executor/run, etc.
 */

import { NextRequest, NextResponse } from "next/server";
import { LambdaClient, InvokeCommand } from "@aws-sdk/client-lambda";

const region = process.env.NEXT_PUBLIC_AWS_REGION || "us-east-1";

function getLambdaClient(): LambdaClient {
    return new LambdaClient({
        region,
        requestHandler: {
            requestTimeout: 600_000, // 10 minutes — analysis Lambda can take a while
        },
    });
}

// Map URL paths to Lambda function names
function getFunctionName(path: string): string {
    if (path.startsWith("executor")) return "perfsage-executor-dev";
    if (path.startsWith("analysis")) return "perfsage-analysis-dev";
    return "perfsage-testgen-dev"; // jobs, health
}

// Build the Lambda event to simulate API Gateway
function buildLambdaEvent(method: string, path: string, body: any): any {
    return {
        httpMethod: method,
        path: `/${path}`,
        body: body ? JSON.stringify(body) : null,
        requestContext: {
            http: { method },
        },
    };
}

export async function POST(
    request: NextRequest,
    { params }: { params: Promise<{ path: string[] }> },
) {
    const path = (await params).path.join("/");
    const functionName = getFunctionName(path);

    let body: any = null;
    try {
        body = await request.json();
    } catch {
        // No body
    }

    try {
        const client = getLambdaClient();
        const event = buildLambdaEvent("POST", path, body);

        const command = new InvokeCommand({
            FunctionName: functionName,
            Payload: new TextEncoder().encode(JSON.stringify(event)),
        });

        const response = await client.send(command);

        if (response.FunctionError) {
            const error = new TextDecoder().decode(response.Payload);
            return NextResponse.json({ error }, { status: 500 });
        }

        const result = JSON.parse(new TextDecoder().decode(response.Payload));

        // Lambda returns { statusCode, body, headers }
        const statusCode = result.statusCode || 200;
        const responseBody = result.body ? JSON.parse(result.body) : result;

        return NextResponse.json(responseBody, { status: statusCode });
    } catch (err: any) {
        return NextResponse.json(
            { error: err.message || "Internal server error" },
            { status: 500 },
        );
    }
}

export async function GET(
    request: NextRequest,
    { params }: { params: Promise<{ path: string[] }> },
) {
    const path = (await params).path.join("/");
    const functionName = getFunctionName(path);

    try {
        const client = getLambdaClient();
        const event = buildLambdaEvent("GET", path, null);

        const command = new InvokeCommand({
            FunctionName: functionName,
            Payload: new TextEncoder().encode(JSON.stringify(event)),
        });

        const response = await client.send(command);

        if (response.FunctionError) {
            const error = new TextDecoder().decode(response.Payload);
            return NextResponse.json({ error }, { status: 500 });
        }

        const result = JSON.parse(new TextDecoder().decode(response.Payload));
        const statusCode = result.statusCode || 200;
        const responseBody = result.body ? JSON.parse(result.body) : result;

        return NextResponse.json(responseBody, { status: statusCode });
    } catch (err: any) {
        return NextResponse.json(
            { error: err.message || "Internal server error" },
            { status: 500 },
        );
    }
}
