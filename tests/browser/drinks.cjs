// Offline checks: no Discord credentials, account, server, or external requests.
const assert = require("node:assert/strict");
const path = require("node:path");
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const root = path.resolve(__dirname, "../../espresso/public");
const site = "https://espresso.test";
async function main() {
  const browser = await chromium.launch({headless:true,channel:process.env.PLAYWRIGHT_CHANNEL || (process.platform === "win32" ? "msedge" : undefined)});
  try {
    const context = await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:"reduce"});
    let failMenu = false;
    await context.route("**/*", async route => {
      const url = new URL(route.request().url());
      if (url.origin !== site) return route.abort();
      if (url.pathname === "/api/stats") return route.fulfill({json:{status:"preview",server_count:null}});
      if (url.pathname === "/assets/drinks.json" && failMenu) return route.fulfill({status:503,body:"Unavailable"});
      const file = url.pathname === "/" ? "index.html" : url.pathname.startsWith("/assets/") ? url.pathname.slice(8) : "";
      if (!/^[a-z0-9.-]+$/.test(file)) return route.fulfill({status:404,body:"Not found"});
      const contentType = {".js":"application/javascript",".css":"text/css",".html":"text/html",".json":"application/json",".png":"image/png"}[path.extname(file)];
      return route.fulfill({path:path.join(root,file),contentType});
    });
    const page = await context.newPage(), errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(site);
    await page.locator("#drink-copy-command:enabled").waitFor();
    assert.equal(await page.locator("[data-drink]").count(),12);
    await page.locator("[data-drink-filter=cocoa]").click();
    assert.equal(await page.locator("[data-drink]").count(),1);
    assert.match(await page.locator("#drink-card-demo").innerText(),/a hot chocolate/);
    assert.match(await page.locator("#drink-list").innerText(),/Coffee-free/);
    await page.locator("#drink-character").selectOption("dreamer");
    assert.match(await page.locator("#drink-card-demo").innerText(),/Cloud Dreamer/);
    await page.locator("#drink-recipient").fill("<img src=x onerror=alert(1)>");
    assert.equal(await page.locator(".drink-card-copy img").count(),0);
    assert.match(await page.locator(".drink-card-message").innerText(),/<img src=x/);
    await page.locator("#drink-recipient").fill("your favorite person");
    await page.locator("#drink-surprise").click();
    assert.match(await page.locator("#drink-menu-status").innerText(),/Hot chocolate/);
    await page.locator("[data-drink-filter=tea]").click();
    assert.equal(await page.locator("[data-drink]").count(),2);
    await page.locator("[data-drink=matcha-latte]").click();
    assert.match(await page.locator("#drink-card-demo").innerText(),/a matcha latte/);
    await page.locator("#drink-character").selectOption("none");
    assert.equal(await page.locator("#drink-card-demo img").count(),0);
    for (const character of ["sabrina","guardian","dreamer","spark"]) {
      await page.locator("#drink-character").selectOption(character);
      await page.waitForFunction(() => {
        const image = document.querySelector("#drink-card-demo img");
        return image.complete && image.naturalWidth > 0;
      });
    }
    await page.locator("[data-drink-filter=all]").click();
    await page.locator("[data-drink=iced-latte]").focus();
    await page.keyboard.press("Enter");
    assert.equal(await page.locator("[data-drink=iced-latte]").getAttribute("aria-pressed"),"true");
    assert.match(await page.locator("#drink-card-demo").innerText(),/an iced latte/);
    await context.grantPermissions(["clipboard-read","clipboard-write"],{origin:site});
    await page.locator("#drink-copy-command").click();
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()),"/drinks");
    for (const width of [1440,768,390,320]) {
      await page.setViewportSize({width,height:900});
      await page.locator("#drink-card-demo").scrollIntoViewIfNeeded();
      if (width < 760) {
        await page.locator("#drink-mobile-choice").selectOption("hot-chocolate");
        assert.match(await page.locator("#drink-card-demo").innerText(),/a hot chocolate/);
      }
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),false,`Overflow at ${width}px`);
    }
    failMenu = true;
    await page.reload();
    await page.locator("#drink-menu-status").filter({hasText:"couldn't load"}).waitFor();
    assert.equal(await page.locator("#drink-copy-command").isDisabled(),true);
    assert.match(await page.locator("#drink-list").innerText(),/hot chocolate/);
    assert.deepEqual(errors,[]);
    console.log("Beverage browser checks passed: filters, real drink details, four artwork files, text safety, keyboard selection, clipboard, mobile layout, and menu failure fallback.");
  } finally {await browser.close();}
}
main().catch(error => {console.error(error);process.exitCode=1;});
