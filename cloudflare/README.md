# Cloudflare Worker - Latency-Based Region Routing

This Cloudflare Worker measures latency to AWS DynamoDB endpoints in different regions and redirects users to the region with the lowest latency.

## Setup

### Prerequisites

1. **Cloudflare Account**: Sign up at [cloudflare.com](https://cloudflare.com)
2. **Node.js**: Install Node.js 18+ and npm
3. **Wrangler CLI**: Cloudflare's CLI tool (installed via npm)

### Installation

```bash
cd cloudflare
npm install
```

### Configuration

1. **Update `src/index.ts`**:
   - Change `hostname` to your actual domain
   - Modify `awsRegions` array to add/remove AWS regions to test
   - Adjust `timeoutMs` in `measureLatency()` if needed (default: 5000ms)

2. **Update `wrangler.toml`**:
   - Set the worker name
   - Configure routes to attach to your domain(s)
   - Add environment variables if needed

3. **Login to Cloudflare**:
   ```bash
   npx wrangler login
   ```

### Development

Test locally:
```bash
npm run dev
```

This starts a local development server. Latency measurements will work, but may differ from production due to different network paths.

### Deployment

Deploy to Cloudflare:
```bash
npm run deploy
```

### Monitoring

View logs in real-time:
```bash
npm run tail
```

## How It Works

1. **Request Flow**: User → Cloudflare Edge → Worker → Latency Measurement → Redirect
2. **Latency Measurement**: 
   - Makes parallel HEAD requests to AWS DynamoDB endpoints in each region
   - Measures response time for each region
   - Selects the region with the lowest latency
3. **Redirect**: Redirects to the subdomain matching the fastest region (e.g., `eu-west-3.example.com`)

## AWS Regions Tested

The worker currently measures latency to:
- `eu-west-3` (Europe - Paris)
- `us-east-1` (US - N. Virginia)
- `ap-southeast-1` (Asia Pacific - Singapore)

## DNS Configuration

You'll need to set up DNS records for your region subdomains:

```
eu-west-3.example.com    → Your API/CDN
us-east-1.example.com    → Your API/CDN
ap-southeast-1.example.com → Your API/CDN
```

## Performance Considerations

- **Latency Measurement**: Each request measures latency to all regions in parallel, which adds ~100-500ms to the response time
- **Timeout**: Default timeout is 5 seconds per region measurement
- **Caching**: Consider implementing caching to avoid measuring latency on every request (see below)

## Optional: Caching Latency Results

To reduce latency overhead, you could cache results:

```typescript
// Cache latency results for 60 seconds
const CACHE_TTL = 60 * 1000;
let cachedResult: { region: string; timestamp: number } | null = null;

// In fetch handler:
if (cachedResult && Date.now() - cachedResult.timestamp < CACHE_TTL) {
    // Use cached result
} else {
    // Measure latency and cache result
}
```

## Testing

Test the worker:

```bash
# Test redirect
curl -I https://example.com/

# The response should redirect to the fastest region
# Location: https://eu-west-3.example.com/ (or whichever is fastest)
```

## Notes

- **302 Redirects**: Using temporary redirects (302) since latency can change over time
- **Path Preservation**: The worker preserves the full path and query string
- **Parallel Measurement**: All regions are measured in parallel for speed
- **Timeout Handling**: Failed or timed-out measurements are treated as infinite latency

