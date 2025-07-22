import nodriver as uc
import asyncio

async def download_deezer_track(deezer_link=None, initial_delay=7.5):
    if deezer_link is None:
        deezer_link = "https://www.deezer.com/us/track/2947516331"
    
    browser = None
    try:
        browser = await uc.start(headless=False)
        page = await browser.get("https://deezmate.com/en")
        
        print("Loading...")
        await asyncio.sleep(initial_delay)
        
        input_selector = 'input[placeholder="Paste your Deezer link here..."]'
        await page.wait_for(input_selector, timeout=15)
        input_element = await page.select(input_selector)
        await input_element.clear_input()
        await input_element.send_keys(deezer_link)
        print("Link entered")
        
        await page.evaluate("""
            window.apiResponse = null;
            window.originalFetch = window.fetch;
            window.fetch = function(...args) {
                return window.originalFetch(...args).then(async response => {
                    if (response.url.includes('api.deezmate.com/dl/')) {
                        try {
                            const data = await response.clone().json();
                            window.apiResponse = data;
                            console.log('Captured API response:', data);
                        } catch (e) {
                            console.log('Error parsing API response:', e);
                        }
                    }
                    return response;
                });
            };
        """)
        
        max_retries = 3
        download_button_clicked = False
        
        for attempt in range(max_retries):
            try:
                download_button_selector = 'button.bg-purple.hover\\:bg-purple-dark.cursor-pointer.transition.text-white.rounded-xl.p-2.mt-2.w-full.mb-5'
                await page.wait_for(download_button_selector, timeout=15)
                download_button = await page.select(download_button_selector)
                await download_button.click()
                print("Processing...")
                download_button_clicked = True
                break
                
            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"Turnstile verification failed, retrying... ({attempt + 1}/{max_retries})")
                    await asyncio.sleep(0.5)
                    await page.evaluate("window.apiResponse = null;")
                else:
                    print("Failed to pass Turnstile verification after all retries")
                    raise e
        
        if not download_button_clicked:
            return None
        
        try:
            track_download_selector = 'button.bg-purple.text-white.flex.items-center.gap-2.px-3.py-1.rounded-full.hover\\:bg-purple-dark.transition'
            await page.wait_for(track_download_selector, timeout=15)
            track_download_button = await page.select(track_download_selector)
            await track_download_button.click()
        except Exception as e:
            print(f"Failed to click track download button: {e}")
            return None
        
        print("Getting FLAC URL from API response...")
        
        api_response = None
        for i in range(30):
            api_response = await page.evaluate("window.apiResponse")
            if api_response:
                break
            await asyncio.sleep(0.2)
        
        if not api_response:
            return None
        
        def parse_nodriver_response(data):
            if isinstance(data, list):
                result = {}
                for item in data:
                    if isinstance(item, list) and len(item) == 2:
                        key = item[0]
                        value_obj = item[1]
                        if isinstance(value_obj, dict) and 'value' in value_obj:
                            if value_obj.get('type') == 'object':
                                result[key] = parse_nodriver_response(value_obj['value'])
                            else:
                                result[key] = value_obj['value']
                return result
            return data
        
        parsed_response = parse_nodriver_response(api_response)
        
        if parsed_response.get('success') and parsed_response.get('links'):
            flac_url = parsed_response['links'].get('flac')
            if flac_url:
                print(f"Successfully obtained FLAC download URL: {flac_url}")
                return flac_url
        
        return None
        
    except Exception as e:
        print(f"Error: {e}")
        return None
    finally:
        if browser:
            try:
                await browser.stop()
            except:
                pass

async def main(deezer_link=None, initial_delay=7.5):
    flac_url = await download_deezer_track(deezer_link, initial_delay)
    if not flac_url:
        print("Failed to download track")
    return flac_url

if __name__ == "__main__":
    uc.loop().run_until_complete(main())