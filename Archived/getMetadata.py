import asyncio
import zendriver as zd

async def get_metadata(page, headless=True):
    max_attempts = 40
    attempts = 0
    
    await asyncio.sleep(2)
    
    await page.evaluate("""
        window.downloadInfo = null;
        const originalFetch = window.fetch;
        window.fetch = async function(...args) {
            const [url, config] = args;
            if (url.includes('/api/load?url=%2Fapi%2Ffetch%2Fstream%2Fv2')) {
                const payload = JSON.parse(config.body);
                const title = document.querySelector('h1.svelte-6pt9ji').textContent.trim();
                const artists = Array.from(document.querySelectorAll('h2.svelte-6pt9ji a.normal'))
                                .map(a => a.textContent.trim())
                                .join(', ');
                const cover = document.querySelector('.svelte-6pt9ji .meta.svelte-6pt9ji a').href;
                                
                window.downloadInfo = {
                    url: payload.url,
                    cover: cover,
                    title: title,
                    artists: artists,
                    token: payload.token.primary,
                    expiry: payload.token.expiry
                };
            }
            return originalFetch.apply(this, args);
        };
    """)
    
    await page.evaluate("""
        function waitForElement(selector) {
            return new Promise(resolve => {
                if (document.querySelector(selector)) {
                    return resolve(document.querySelector(selector));
                }

                const observer = new MutationObserver(mutations => {
                    if (document.querySelector(selector)) {
                        observer.disconnect();
                        resolve(document.querySelector(selector));
                    }
                });

                observer.observe(document.documentElement, {
                    childList: true,
                    subtree: true
                });
            });
        }

        (async () => {
            if (!window.location.hostname.includes('lucida.')) return;
            
            await Promise.race([
                waitForElement('.d1-track button'),
                waitForElement('button[class*="download-button"]')
            ]);

            const clickDownloadButton = () => {
                const button = document.querySelector('.d1-track button') || 
                              document.querySelector('button[class*="download-button"]');
                if (button) button.click();
            };

            clickDownloadButton();
        })();
    """)
    
    while attempts < max_attempts:
        download_info = await page.evaluate("window.downloadInfo")
        if download_info:
            return download_info
        
        await asyncio.sleep(0.5)
        attempts += 1
    
    raise TimeoutError("Timeout")

async def main(headless=True):
    browser = await zd.start(headless=headless)
    try:
        track_id = "2plbrEY59IikOBgBGLjaoe"
        url = f"https://lucida.to/?url=https%3A%2F%2Fopen.spotify.com%2Ftrack%2F{track_id}&country=auto&to=tidal"
        
        page = await browser.get(url)
        download_info = await get_metadata(page)
        print(download_info)
        return download_info
    finally:
        await browser.stop()

if __name__ == "__main__":
    asyncio.run(main())
