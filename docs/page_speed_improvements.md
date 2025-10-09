# Page Speed Insights Improvements

This document details the page speed improvements made to Scryfall OS and suggests additional potential enhancements.

## Implemented Improvements

### 1. Meta Description (SEO)
**Impact**: High for SEO, medium for page speed score

**Implementation**:
```html
<meta name="description" content="Search for Magic: The Gathering cards. Find MTG cards by name, type, text, mana cost, and more. Open source Scryfall implementation." />
```

**Benefits**:
- Improves SEO rankings
- Better search result snippets
- Helps with social sharing previews

### 2. Theme Color Meta Tag
**Impact**: Low to medium for mobile experience

**Implementation**:
```html
<meta name="theme-color" content="#667eea" />
```

**Benefits**:
- Better mobile browser integration
- Consistent branding in mobile UI
- Improves perceived performance on mobile

### 3. Favicon Link Tag
**Impact**: Low for page speed, medium for UX

**Implementation**:
```html
<link rel="icon" type="image/x-icon" href="/favicon.ico" />
```

**Benefits**:
- Explicit reference prevents 404 errors
- Better browser caching
- Faster favicon loading

### 4. Image Width/Height Attributes
**Impact**: High for Cumulative Layout Shift (CLS)

**Implementation**:
```javascript
// Card images (normal size: 410x573)
const imageHtml = `<img class="card-image" ... width="410" height="573" ... />`;

// Modal images (large size: 745x1040)
const imageHtml = `<img class="modal-image" ... width="745" height="1040" ... />`;
```

**Benefits**:
- Prevents layout shift when images load
- Significantly improves CLS score
- Better user experience (no content jumping)

**Technical Details**:
- MTG card aspect ratio: 5:7 (0.715)
- Small images: 220x307
- Normal images: 410x573
- Large images: 745x1040

### 5. Lazy Loading Images
**Impact**: High for initial page load time

**Implementation**:
```javascript
// Only first row gets eager loading for LCP
const fetchPriorityAttr = isFirstRow ? ' fetchpriority="high"' : '';
const loadingAttr = isFirstRow ? '' : ' loading="lazy"';
```

**Benefits**:
- Reduces initial payload
- Faster Time to Interactive (TTI)
- Better Largest Contentful Paint (LCP) for first row
- Deferred loading for below-the-fold content

### 6. Preconnect Optimization
**Impact**: Low to medium for connection time

**Implementation**:
```html
<link rel="preconnect" href="https://cards.scryfall.io" />
<link rel="preconnect" href="https://cdn.jsdelivr.net" />
<link rel="preconnect" href="https://d1hot9ps2xugbc.cloudfront.net" />
```

**Benefits**:
- Faster DNS resolution
- Earlier TCP handshake
- Reduced latency for external resources

**Note**: Removed unnecessary `crossorigin` attributes (only needed for CORS requests).

## Additional Potential Improvements

### High Impact (Not Yet Implemented)

#### 1. HTTP Caching Headers
**Current State**: No explicit cache headers for static files

**Suggested Implementation**:
```python
# In api_resource.py, update index_html method:
def index_html(self, *, falcon_response: falcon.Response | None = None, **_: object) -> None:
    self._serve_static_file(filename="index.html", falcon_response=falcon_response)
    falcon_response.content_type = "text/html"
    # Add cache headers
    falcon_response.set_header("Cache-Control", "public, max-age=3600")  # 1 hour
    falcon_response.set_header("ETag", generate_etag_for_file("index.html"))
```

**Benefits**:
- Reduced server requests on repeat visits
- Better browser caching
- Lower bandwidth usage

#### 2. Content Compression
**Current State**: Middleware exists but effectiveness depends on configuration

**Suggested Verification**:
- Ensure brotli compression is enabled (best compression ratio)
- Verify gzip fallback is working
- Check compression thresholds are appropriate

**Already Implemented**:
- `api/middlewares/compression/` provides Brotli, Gzip, and Zstd compression
- Middleware is configured in the application

#### 3. Resource Hints for Mana Font
**Current State**: Using `media="print"` trick with `onload` for async loading

**Alternative Approach**:
```html
<link rel="preload" href="https://cdn.jsdelivr.net/npm/mana-font@latest/css/mana.min.css" as="style" onload="this.onload=null;this.rel='stylesheet'" />
```

**Benefits**:
- More explicit async loading
- Better browser support
- Clearer intent

### Medium Impact (Consider for Future)

#### 4. Service Worker for Offline Support
**Implementation**: Add service worker for caching

**Benefits**:
- Offline functionality
- Faster repeat visits
- Better PWA score

#### 5. Manifest.json for PWA
**Implementation**: Create web app manifest

**Benefits**:
- Installable web app
- Better mobile experience
- Higher PWA score

#### 6. Open Graph Meta Tags
**Implementation**:
```html
<meta property="og:title" content="Card Search - Scryfall OS" />
<meta property="og:description" content="Search for Magic: The Gathering cards" />
<meta property="og:image" content="/og-image.png" />
<meta property="og:type" content="website" />
```

**Benefits**:
- Better social media sharing
- More attractive link previews
- Increased engagement

#### 7. Apple Touch Icon
**Implementation**:
```html
<link rel="apple-touch-icon" href="/apple-touch-icon.png" />
```

**Benefits**:
- Better iOS home screen appearance
- Professional look on Apple devices

### Low Impact (Nice to Have)

#### 8. Inline Critical CSS
**Current State**: 15KB inline CSS (reasonable size)

**Potential Optimization**:
- Extract above-the-fold CSS
- Defer non-critical styles
- Use CSS containment

**Note**: Current implementation is already reasonable for a single-page app.

#### 9. DNS Prefetch for Additional Resources
**Implementation**:
```html
<link rel="dns-prefetch" href="https://example.com" />
```

**Benefits**:
- Earlier DNS resolution
- Marginal performance gain

#### 10. Structured Data (JSON-LD)
**Implementation**: Add schema.org structured data for search engines

**Benefits**:
- Rich search results
- Better SEO
- More informative snippets

## Performance Metrics to Monitor

### Core Web Vitals
1. **Largest Contentful Paint (LCP)**: Target < 2.5s
   - Optimized with `fetchpriority="high"` on first row images
   - Should improve with image dimensions

2. **First Input Delay (FID)**: Target < 100ms
   - JavaScript is loaded at end of body (good)
   - Minimal blocking scripts

3. **Cumulative Layout Shift (CLS)**: Target < 0.1
   - Fixed with width/height attributes on images
   - Should see significant improvement

### Other Metrics
- **First Contentful Paint (FCP)**: Target < 1.8s
- **Time to Interactive (TTI)**: Target < 3.8s
  - Improved with lazy loading
- **Total Blocking Time (TBT)**: Target < 200ms
- **Speed Index**: Target < 3.4s

## Testing Recommendations

1. **Before/After Comparison**:
   - Run PageSpeed Insights on both versions
   - Compare Core Web Vitals
   - Check mobile vs desktop scores

2. **Real User Monitoring**:
   - Track actual user metrics
   - Monitor field data
   - Identify bottlenecks

3. **Lighthouse Audits**:
   - Run regular Lighthouse tests
   - Check all categories (Performance, Accessibility, Best Practices, SEO)
   - Monitor score trends

## Implementation Notes

### Files Modified
- `api/index.html`: All visual/HTML changes

### Tests Passed
- `api/tests/test_api_resource.py::TestAPIResourceStaticFileServing`: 3/3 passed
- All existing functionality maintained

### No Breaking Changes
- Fully backward compatible
- All existing features work unchanged
- No API changes

## References

- [Google PageSpeed Insights](https://pagespeed.web.dev/)
- [Web.dev Core Web Vitals](https://web.dev/vitals/)
- [MDN Web Performance](https://developer.mozilla.org/en-US/docs/Web/Performance)
- [Lighthouse Documentation](https://developer.chrome.com/docs/lighthouse/)
