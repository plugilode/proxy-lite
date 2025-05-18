import asyncio
import csv
from playwright.async_api import async_playwright
import re

BASE_URL = "https://www.billiger.de/shops"
LETTERS = [chr(i) for i in range(ord('A'), ord('Z')+1)]

CSV_FIELDS = [
    'title', 'url', 'address', 'house_number', 'zip', 'city', 'country',
    'phone', 'email', 'director_decision_maker', 'tax_id', 'tag_category', 'products'
]

def extract_address(address_text):
    # Try to extract house number, zip, city, country from address
    house_number = zip_code = city = country = ''
    if address_text:
        # Example: Musterstraße 1, 12345 Musterstadt, Deutschland
        m = re.match(r"(.+?)\s(\d+),\s*(\d{5})\s(.+?),?\s*(.*)", address_text)
        if m:
            address, house_number, zip_code, city, country = m.groups()
            return address, house_number, zip_code, city, country
    return address_text, house_number, zip_code, city, country

async def scrape_shops():
    shops = []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(BASE_URL)
        await page.wait_for_selector('.shop-list')

        # Scrape shops for 'A' (default page)
        shops += await extract_shops_from_page(page)

        # Scrape shops for B-Z
        for letter in LETTERS[1:]:
            letter_url = f"{BASE_URL}/{letter}"
            await page.goto(letter_url)
            await page.wait_for_selector('.shop-list')
            shops += await extract_shops_from_page(page)

        await browser.close()
    return shops

async def extract_shops_from_page(page):
    shop_elements = await page.query_selector_all('.shop-list .shop-list-entry a.shop-list-entry__link')
    shops = []
    for el in shop_elements:
        title = (await el.get_attribute('title')) or (await el.inner_text())
        url = await el.get_attribute('href')
        if url and not url.startswith('http'):
            url = 'https://www.billiger.de' + url
        shops.append({'title': title.strip(), 'url': url.strip() if url else ''})
    return shops

async def extract_shop_details(context, shop):
    # Visit the shop detail page and extract required fields
    data = {field: '' for field in CSV_FIELDS}
    data['title'] = shop['title']
    data['url'] = shop['url']
    page = await context.new_page()
    try:
        await page.goto(shop['url'], timeout=30000)
        await page.wait_for_selector('body', timeout=10000)
        # Address block
        address_text = ''
        address_el = await page.query_selector('.shop-details__address')
        if address_el:
            address_text = (await address_el.inner_text()).replace('\n', ', ').strip()
        address, house_number, zip_code, city, country = extract_address(address_text)
        data['address'] = address
        data['house_number'] = house_number
        data['zip'] = zip_code
        data['city'] = city
        data['country'] = country
        # Phone
        phone_el = await page.query_selector('a[href^="tel:"]')
        if phone_el:
            data['phone'] = (await phone_el.inner_text()).strip()
        # Email
        email_el = await page.query_selector('a[href^="mailto:"]')
        if email_el:
            data['email'] = (await email_el.inner_text()).strip()
        # Director/decision maker, tax-id, tag category
        info_els = await page.query_selector_all('.shop-details__info, .shop-details__info-list li')
        for el in info_els:
            text = (await el.inner_text()).strip()
            if 'Geschäftsführer' in text or 'Inhaber' in text or 'Entscheider' in text:
                data['director_decision_maker'] = text
            if 'USt-IdNr' in text or 'Steuernummer' in text or 'Tax' in text:
                data['tax_id'] = text
            if 'Kategorie' in text or 'Branche' in text:
                data['tag_category'] = text
        # Products (top 5)
        product_els = await page.query_selector_all('.shop-details__products-list li, .shop-details__products-list a')
        products = []
        for el in product_els[:5]:
            products.append((await el.inner_text()).strip())
        data['products'] = '; '.join(products)
    except Exception as e:
        print(f"Error scraping {shop['url']}: {e}")
    finally:
        await page.close()
    return data

async def main():
    shops = await scrape_shops()
    print(f"Found {len(shops)} shops. Scraping details...")
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context()
        results = []
        for i, shop in enumerate(shops):
            print(f"[{i+1}/{len(shops)}] {shop['title']}")
            details = await extract_shop_details(context, shop)
            results.append(details)
            # Print a summary of the extracted data for logs
            summary = f"Shop: {details['title']} | URL: {details['url']} | Address: {details['address']} | ZIP: {details['zip']} | City: {details['city']} | Country: {details['country']} | Phone: {details['phone']} | Email: {details['email']} | Director/Decision Maker: {details['director_decision_maker']} | Tax-ID: {details['tax_id']} | Category: {details['tag_category']} | Products: {details['products']}"
            print(summary)
        await browser.close()
    with open('billiger_shops.csv', 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for shop in results:
            writer.writerow(shop)
    print(f"Scraped {len(results)} shops to billiger_shops.csv")

if __name__ == "__main__":
    asyncio.run(main()) 