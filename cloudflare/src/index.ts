/// <reference types="@cloudflare/workers-types" />

/**
 * Cloudflare Worker for latency-based region routing
 * 
 * This worker measures latency to AWS DynamoDB endpoints in different regions
 * and redirects users to the region with the lowest latency.
 */

const hostname = "arcane-tutor.com";

// AWS DynamoDB endpoints to measure latency against
const awsRegions = [
    { region: "eu-west-3", url: "https://dynamodb.eu-west-3.amazonaws.com/" },
    { region: "us-east-1", url: "https://dynamodb.us-east-1.amazonaws.com/" },
    { region: "ap-southeast-1", url: "https://dynamodb.ap-southeast-1.amazonaws.com/" },
] as const;

// Environment interface (for Cloudflare Workers bindings)
interface Env {
    // Add any environment variables here
    // For example: API_URL: string;
}

/**
 * Measure latency to a URL by making a HEAD request
 * Returns the latency in milliseconds, or Infinity if the request fails
 */
async function measureLatency(url: string, timeoutMs: number = 5000): Promise<number> {
    const startTime = Date.now();
    try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
        
        const response = await fetch(url, {
            method: "HEAD",
            signal: controller.signal,
        });
        
        clearTimeout(timeoutId);
        const latency = Date.now() - startTime;
        
        // Only consider successful responses (2xx, 3xx, 4xx are all fine for latency testing)
        // 5xx might indicate issues, but we'll still use the latency measurement
        return latency;
    } catch (error) {
        // Request failed or timed out
        return Infinity;
    }
}

/**
 * Find the region with the lowest latency
 * Measures latency to all regions in parallel
 */
async function findFastestRegion(): Promise<string> {
    // Measure latency to all regions in parallel
    const latencyPromises = awsRegions.map(async (region) => ({
        region: region.region,
        latency: await measureLatency(region.url),
    }));
    
    const results = await Promise.all(latencyPromises);
    
    // Find the region with the lowest latency
    const fastest = results.reduce((prev, current) => 
        current.latency < prev.latency ? current : prev
    );
    
    return fastest.region;
}

/**
 * Main fetch handler for the Cloudflare Worker
 */
export default {
    async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
        const url = new URL(request.url);
        
        // Find the fastest region based on latency
        const fastestRegion = await findFastestRegion();
        
        // Redirect to the region-specific subdomain
        const redirectUrl = `https://${fastestRegion}.${hostname}${url.pathname}${url.search}`;
        return Response.redirect(redirectUrl, 302); // Using 302 (temporary) since latency can change
    },
} satisfies ExportedHandler<Env>;

