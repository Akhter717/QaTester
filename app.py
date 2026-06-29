import streamlit as st
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import zipfile
import io
import re
import pandas as pd
from groq import Groq

# ─────────────────────────────────────────────
# PAGE CONFIG
# ─────────────────────────────────────────────
st.set_page_config(page_title="Selenium Test Generator", page_icon="🤖", layout="wide")

st.title("🤖 AI Selenium Test Generator")
st.caption("Crawl any website → Test Plan → Test Cases → Locators → Java Code → Download")

# ─────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Configuration")
    groq_api_key = st.text_input("Groq API Key", type="password", placeholder="gsk_...")
    st.markdown("---")
    st.markdown("**Steps:**")
    st.markdown("1️⃣ Enter Groq API Key")
    st.markdown("2️⃣ Enter website URL & click Start")
    st.markdown("3️⃣ Tab 1 → Test Plan")
    st.markdown("4️⃣ Tab 2 → Test Cases")
    st.markdown("5️⃣ Tab 3 → Extract Locators")
    st.markdown("6️⃣ Tab 4 → Generate Java Code")
    st.markdown("7️⃣ Tab 5 → Download")
    st.markdown("---")
    st.caption("Free Groq API: [console.groq.com](https://console.groq.com)")

# ─────────────────────────────────────────────
# SESSION STATE
# ─────────────────────────────────────────────
defaults = {
    "crawled_pages": {},
    "test_plan":     "",
    "test_cases":    "",
    "locators":      None,   # None = not run yet
    "java_code":     {},
    "crawl_done":    False,
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ─────────────────────────────────────────────
# HELPER: BUILD LOCATORS FOR ONE ELEMENT
# ─────────────────────────────────────────────
def build_locators(tag, elem):
    eid   = elem.get("id", "").strip()
    ename = elem.get("name", "").strip()
    eclasses = elem.get("class", [])
    etype = elem.get("type", "").strip()
    eplace = elem.get("placeholder", "").strip()
    evalue = elem.get("value", "").strip()
    earia  = elem.get("aria-label", "").strip()
    etitle = elem.get("title", "").strip()
    edata  = elem.get("data-test", "").strip()
    etext  = elem.get_text(strip=True)[:40]

    # CSS
    if eid:
        css = f"#{eid}"
    elif edata:
        css = f"[data-test='{edata}']"
    elif ename:
        css = f"{tag}[name='{ename}']"
    elif earia:
        css = f"[aria-label='{earia}']"
    elif eplace:
        css = f"{tag}[placeholder='{eplace}']"
    elif etype:
        css = f"{tag}[type='{etype}']"
    elif eclasses:
        css = f"{tag}.{'.'.join(eclasses[:2])}"
    elif evalue and tag == "input":
        css = f"input[value='{evalue}']"
    else:
        css = tag

    # XPath
    if eid:
        xpath = f"//{tag}[@id='{eid}']"
    elif edata:
        xpath = f"//{tag}[@data-test='{edata}']"
    elif ename:
        xpath = f"//{tag}[@name='{ename}']"
    elif earia:
        xpath = f"//{tag}[@aria-label='{earia}']"
    elif eplace:
        xpath = f"//{tag}[@placeholder='{eplace}']"
    elif etext and tag in ["button", "a", "label"]:
        safe = etext.replace("'", "\\'")[:30]
        xpath = f"//{tag}[normalize-space()='{safe}']"
    elif etitle:
        xpath = f"//{tag}[@title='{etitle}']"
    elif etype:
        xpath = f"//{tag}[@type='{etype}']"
    elif eclasses:
        xpath = f"//{tag}[contains(@class,'{eclasses[0]}')]"
    elif evalue and tag == "input":
        xpath = f"//input[@value='{evalue}']"
    else:
        xpath = f"//{tag}"

    return css, xpath

# ─────────────────────────────────────────────
# HELPER: CRAWL WEBSITE
# ─────────────────────────────────────────────
def crawl_website(start_url, max_pages=15):
    visited   = {}
    to_visit  = [start_url]
    base_domain = urlparse(start_url).netloc
    headers   = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    progress  = st.progress(0, text="Starting crawl...")
    count     = 0

    while to_visit and count < max_pages:
        url = to_visit.pop(0)
        if url in visited:
            continue
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            if "text/html" not in resp.headers.get("Content-Type", ""):
                continue
            html = resp.text
            visited[url] = html
            count += 1
            progress.progress(min(count / max_pages, 1.0), text=f"Crawling ({count}): {url}")
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.find_all("a", href=True):
                full = urljoin(url, a["href"])
                p = urlparse(full)
                if (p.netloc == base_domain and p.scheme in ["http","https"]
                        and "#" not in full and full not in visited and full not in to_visit):
                    to_visit.append(full)
        except Exception:
            continue

    progress.empty()
    return visited

# ─────────────────────────────────────────────
# HELPER: EXTRACT LOCATORS
# ─────────────────────────────────────────────
def extract_locators(pages_dict):
    locators = []
    tags     = ["input","button","a","select","textarea","label"]
    seen     = set()

    for url, html in pages_dict.items():
        soup      = BeautifulSoup(html, "html.parser")
        page_name = urlparse(url).path or "/"

        for tag in tags:
            for elem in soup.find_all(tag)[:20]:
                etext  = elem.get_text(strip=True)[:50]
                etype  = elem.get("type", tag)
                eplace = elem.get("placeholder", "")

                label = (etext or eplace or elem.get("aria-label","")
                         or elem.get("name","") or elem.get("id","")
                         or elem.get("href","")[:30] or f"<{tag}>")

                css, xpath = build_locators(tag, elem)

                if css == tag and xpath == f"//{tag}":
                    continue

                key = f"{page_name}|{css}|{xpath}"
                if key in seen:
                    continue
                seen.add(key)

                locators.append({
                    "Page":         page_name,
                    "Tag":          tag,
                    "Type":         etype,
                    "Text / Label": label[:40],
                    "CSS Selector": css,
                    "XPath":        xpath,
                    "_url":         url
                })

    return locators

# ─────────────────────────────────────────────
# HELPER: CALL GROQ
# ─────────────────────────────────────────────
def call_groq(api_key, prompt, system_msg="You are a QA expert."):
    client = Groq(api_key=api_key)
    resp   = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role":"system","content":system_msg},
                  {"role":"user","content":prompt}],
        temperature=0.3,
        max_tokens=4000
    )
    return resp.choices[0].message.content

# ─────────────────────────────────────────────
# HELPER: PAGE SUMMARY (shared by plan + cases)
# ─────────────────────────────────────────────
def build_page_summary(pages_dict):
    summary = ""
    for url, html in list(pages_dict.items())[:8]:
        soup    = BeautifulSoup(html, "html.parser")
        title   = soup.title.string.strip() if soup.title else url
        forms   = len(soup.find_all("form"))
        buttons = len(soup.find_all("button"))
        inputs  = len(soup.find_all("input"))
        links   = len(soup.find_all("a", href=True))
        summary += (f"\nURL: {url}\nTitle: {title}\n"
                    f"Forms: {forms} | Buttons: {buttons} | Inputs: {inputs} | Links: {links}\n")
    return summary

# ─────────────────────────────────────────────
# HELPER: GENERATE TEST PLAN  (high-level doc)
# ─────────────────────────────────────────────
def generate_test_plan(api_key, pages_dict, locators):
    summary    = build_page_summary(pages_dict)
    base_url   = list(pages_dict.keys())[0] if pages_dict else "the website"
    page_count = len(pages_dict)
    loc_count  = len(locators)

    prompt = f"""
You are a senior QA engineer. Write a formal TEST PLAN document for the website below.
A Test Plan is a HIGH-LEVEL strategy document — do NOT write individual test cases inside it.

Website: {base_url}
Pages Crawled: {page_count}
Elements Found: {loc_count}

Pages:
{summary}

Write the Test Plan with EXACTLY these sections in order:

## 1. Introduction
What the website does and the purpose of this test plan.

## 2. Scope of Testing
What WILL be tested and what will NOT be tested.

## 3. Test Objectives
Bullet points listing main goals of testing.

## 4. Testing Types Covered
List only the relevant types:
- Functional Testing
- UI/UX Testing
- Form Validation Testing
- Navigation Testing
- Regression Testing

## 5. Test Environment
- Browser(s) to be used
- Automation Tools (Selenium, TestNG, Maven)
- Operating System

## 6. Entry & Exit Criteria
**Entry Criteria:** (conditions before testing starts)
**Exit Criteria:** (conditions when testing is complete)

## 7. Risks & Mitigation
List 3 risks with mitigation for each.

## 8. Deliverables
What outputs will be produced after testing.

Keep every section short, professional, and specific to the website found above.
"""
    return call_groq(api_key, prompt, "You are a senior QA engineer writing a formal test plan document.")

# ─────────────────────────────────────────────
# HELPER: GENERATE TEST CASES (structured)
# ─────────────────────────────────────────────
def generate_test_cases(api_key, pages_dict, locators):
    summary = build_page_summary(pages_dict)
    loc_info = "\n".join([
        f"  [{l['Tag']}] '{l['Text / Label']}' | CSS: {l['CSS Selector']} | Page: {l['Page']}"
        for l in locators[:30]
    ])

    prompt = f"""
You are a senior QA engineer. Write structured individual test cases for the website below.

Pages found:
{summary}

Interactive elements found:
{loc_info if loc_info else "Elements not yet extracted — base test cases on page structure above."}

Write at least 12 test cases covering:
- Functional UI Testing
- Form Validation Testing
- Navigation & Link Testing
- Login/Auth Flow (only if login form exists on the site)

Use EXACTLY this format for every test case.
Put a horizontal line (---) before each test case.
Number them in sequence from TC_001.

---
**TC_ID:** TC_001
**Summary:** One sentence describing what is being tested
**Page:** Page name or path (e.g. /login, /products)
**Prerequisites:** What must be ready before this test runs
**Test Steps:**
  1. First step
  2. Second step
  3. Third step
**Expected Result:** What should happen when the test passes
**Priority:** High / Medium / Low
**Type:** Functional / Validation / Navigation / Auth

---
**TC_ID:** TC_002
... and so on

Be specific to the actual pages and elements found. Do not write generic test cases.
"""
    return call_groq(api_key, prompt, "You are a senior QA engineer writing structured test cases in standard format.")

# ─────────────────────────────────────────────
# HELPER: GENERATE JAVA CODE
# ─────────────────────────────────────────────
def generate_java_code(api_key, locators, pages_dict):
    java_files = {}
    pages = {}
    for loc in locators[:50]:
        page = loc["Page"].strip("/").replace("/","_").replace("-","_") or "home"
        pages.setdefault(page, []).append(loc)

    for page_name, page_locs in list(pages.items())[:4]:
        class_name = "".join(w.capitalize() for w in re.split(r"[_\-\s]+", page_name) if w) + "Page"
        elements_info = "\n".join([
            f'  [{l["Tag"]}] label="{l["Text / Label"]}" CSS="{l["CSS Selector"]}" XPath="{l["XPath"]}"'
            for l in page_locs[:12]
        ])
        prompt = f"""
Generate a complete Selenium Java Page Object Model class named {class_name}.

Elements:
{elements_info}

Requirements:
- Package: pages
- Use @FindBy with CSS selector (use XPath as fallback)
- Constructor takes WebDriver, calls PageFactory.initElements(driver, this)
- One action method per element (click, sendKeys, getText)
- Include ALL imports
- Return ONLY valid Java code. No explanation. No markdown fences.
"""
        code = call_groq(api_key, prompt, "You are a Selenium Java expert. Return only clean Java code, no markdown.")
        code = re.sub(r"```(?:java)?|```", "", code).strip()
        java_files[f"src/main/java/pages/{class_name}.java"] = code

    page_classes = [
        "".join(w.capitalize() for w in re.split(r"[_\-\s]+", p) if w) + "Page"
        for p in list(pages.keys())[:4]
    ]
    sample = "\n".join([
        f'  [{l["Tag"]}] "{l["Text / Label"]}" CSS: {l["CSS Selector"]}'
        for l in locators[:20]
    ])
    test_prompt = f"""
Generate a complete Selenium Java TestNG test class named WebAppTest.

Page Object classes: {", ".join(page_classes)}
Key elements:
{sample}

Requirements:
- Package: tests
- @BeforeClass: setup ChromeDriver using WebDriverManager
- @AfterClass: quit driver
- Exactly 6 @Test methods:
  1. testPageTitle() — verify page title not empty
  2. testNavigationLinks() — click nav links, verify page loads
  3. testFormInputs() — fill and submit a form
  4. testButtonClicks() — click buttons, verify no crash
  5. testDataDriven() — @DataProvider with 2 data sets
  6. testElementsVisible() — assert key elements are displayed
- Use Assert for all checks
- Include ALL imports
- Return ONLY valid Java code. No explanation. No markdown fences.
"""
    test_code = call_groq(api_key, test_prompt, "You are a Selenium Java TestNG expert. Return only clean Java code, no markdown.")
    test_code = re.sub(r"```(?:java)?|```", "", test_code).strip()
    java_files["src/test/java/tests/WebAppTest.java"] = test_code

    java_files["pom.xml"] = """\
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0
         http://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.selenium.tests</groupId>
  <artifactId>ai-generated-tests</artifactId>
  <version>1.0-SNAPSHOT</version>
  <dependencies>
    <dependency>
      <groupId>org.seleniumhq.selenium</groupId>
      <artifactId>selenium-java</artifactId>
      <version>4.18.1</version>
    </dependency>
    <dependency>
      <groupId>org.testng</groupId>
      <artifactId>testng</artifactId>
      <version>7.9.0</version>
      <scope>test</scope>
    </dependency>
    <dependency>
      <groupId>io.github.bonigarcia</groupId>
      <artifactId>webdrivermanager</artifactId>
      <version>5.7.0</version>
    </dependency>
  </dependencies>
  <build>
    <plugins>
      <plugin>
        <groupId>org.apache.maven.plugins</groupId>
        <artifactId>maven-surefire-plugin</artifactId>
        <version>3.2.5</version>
      </plugin>
    </plugins>
  </build>
</project>"""

    return java_files

# ─────────────────────────────────────────────
# HELPER: ZIP
# ─────────────────────────────────────────────
def create_zip(java_files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, content in java_files.items():
            zf.writestr(path, content)
    buf.seek(0)
    return buf

# ─────────────────────────────────────────────
# MAIN UI — URL INPUT
# ─────────────────────────────────────────────
st.markdown("### 🌐 Enter Website URL")
c1, c2 = st.columns([3, 1])
with c1:
    url_input = st.text_input("URL", placeholder="https://automationexercise.com", label_visibility="collapsed")
with c2:
    start_btn = st.button("🚀 Start", use_container_width=True, type="primary")

if start_btn:
    if not groq_api_key:
        st.error("❌ Please enter your Groq API Key in the sidebar.")
        st.stop()
    if not url_input or not url_input.startswith("http"):
        st.error("❌ Please enter a valid URL starting with http:// or https://")
        st.stop()

    for k, v in defaults.items():
        st.session_state[k] = v

    with st.spinner("🔍 Crawling website..."):
        pages = crawl_website(url_input, max_pages=15)
        st.session_state.crawled_pages = pages
        st.session_state.crawl_done   = True

    st.success(f"✅ Crawl complete! Found **{len(pages)} pages**. Continue in the tabs below.")

# ─────────────────────────────────────────────
# TABS
# ─────────────────────────────────────────────
if st.session_state.crawl_done:

    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "📋 Step 1: Test Plan",
        "🧪 Step 2: Test Cases",
        "🎯 Step 3: Locators",
        "☕ Step 4: Java Code",
        "📥 Step 5: Download"
    ])

    # ── TAB 1: TEST PLAN ────────────────────────
    with tab1:
        st.subheader("📋 Test Plan")
        st.caption("High-level strategy document — scope, objectives, environment, risks. Not individual test cases.")

        with st.expander("🌐 Crawled Pages"):
            for i, u in enumerate(st.session_state.crawled_pages.keys(), 1):
                st.write(f"{i}. {u}")

        if st.session_state.test_plan:
            st.markdown(st.session_state.test_plan)
            if st.button("🔄 Regenerate Test Plan"):
                st.session_state.test_plan = ""
                st.rerun()
        else:
            st.info("Generates a formal test plan document with scope, objectives, risks and deliverables.")
            if st.button("📋 Generate Test Plan", type="primary"):
                with st.spinner("AI is writing the test plan..."):
                    st.session_state.test_plan = generate_test_plan(
                        groq_api_key,
                        st.session_state.crawled_pages,
                        st.session_state.locators or []
                    )
                st.rerun()

    # ── TAB 2: TEST CASES ───────────────────────
    with tab2:
        st.subheader("🧪 Test Cases")
        st.caption("Individual test cases in standard QA format: Summary → Prerequisites → Steps → Expected Result → Priority → Type.")

        if st.session_state.test_cases:
            st.markdown(st.session_state.test_cases)
            if st.button("🔄 Regenerate Test Cases"):
                st.session_state.test_cases = ""
                st.rerun()
        else:
            st.info("Generates structured test cases numbered TC_001, TC_002... in the correct format.")
            if st.button("🧪 Generate Test Cases", type="primary"):
                with st.spinner("AI is writing test cases..."):
                    st.session_state.test_cases = generate_test_cases(
                        groq_api_key,
                        st.session_state.crawled_pages,
                        st.session_state.locators or []
                    )
                st.rerun()

    # ── TAB 3: LOCATORS ─────────────────────────
    with tab3:
        st.subheader("🎯 Element Locators — XPath & CSS")
        st.caption("Extracted from crawled HTML. Both XPath and CSS for every interactive element.")

        if st.button("🔍 Extract Locators", type="primary"):
            with st.spinner("Scanning all pages for interactive elements..."):
                st.session_state.locators = extract_locators(st.session_state.crawled_pages)

        if st.session_state.locators is not None:
            locs = st.session_state.locators
            if len(locs) == 0:
                st.warning(
                    "⚠️ No locators found. The site may load via JavaScript. "
                    "Try **https://the-internet.herokuapp.com** or **https://automationbookstore.dev**."
                )
            else:
                st.success(f"✅ Found **{len(locs)} elements** across all pages.")
                df = pd.DataFrame(locs)
                pages_opt = ["All Pages"] + sorted(df["Page"].unique().tolist())
                sel = st.selectbox("Filter by Page", pages_opt)
                filtered = df if sel == "All Pages" else df[df["Page"] == sel]
                cols = ["Page","Tag","Type","Text / Label","CSS Selector","XPath"]
                st.dataframe(filtered[cols], use_container_width=True, height=400)
                st.caption(f"Showing {len(filtered)} of {len(locs)} elements")

    # ── TAB 4: JAVA CODE ────────────────────────
    with tab4:
        st.subheader("☕ Generated Java Code")
        st.caption("POM Page Object classes + TestNG Data-Driven test class.")

        if st.session_state.locators is None:
            st.warning("⚠️ Go to Step 3 and extract locators first.")
        elif len(st.session_state.locators) == 0:
            st.warning("⚠️ No locators found. Cannot generate code.")
        elif st.session_state.java_code:
            for fname, code in st.session_state.java_code.items():
                lang = "java" if fname.endswith(".java") else "xml"
                with st.expander(f"📄 {fname}"):
                    st.code(code, language=lang)
            if st.button("🔄 Regenerate Code"):
                st.session_state.java_code = {}
                st.rerun()
        else:
            st.info(f"Ready to generate from **{len(st.session_state.locators)} elements**.")
            if st.button("⚙️ Generate Java Code", type="primary"):
                with st.spinner("AI is generating Selenium Java code... (~30 seconds)"):
                    st.session_state.java_code = generate_java_code(
                        groq_api_key,
                        st.session_state.locators,
                        st.session_state.crawled_pages
                    )
                st.rerun()

    # ── TAB 5: DOWNLOAD ─────────────────────────
    with tab5:
        st.subheader("📥 Download Your Test Suite")

        if not st.session_state.java_code:
            st.warning("⚠️ Please generate Java code in Step 4 first.")
        else:
            st.success(f"✅ {len(st.session_state.java_code)} files ready.")

            c1, c2 = st.columns(2)
            with c1:
                st.download_button(
                    label="📦 Download Full Maven Project (.zip)",
                    data=create_zip(st.session_state.java_code),
                    file_name="selenium-tests.zip",
                    mime="application/zip",
                    use_container_width=True,
                    type="primary"
                )
            with c2:
                test_code = next(
                    (c for f, c in st.session_state.java_code.items() if "WebAppTest.java" in f), ""
                )
                if test_code:
                    st.download_button(
                        label="☕ Download WebAppTest.java only",
                        data=test_code,
                        file_name="WebAppTest.java",
                        mime="text/plain",
                        use_container_width=True
                    )

            st.markdown("---")
            st.markdown("**📋 Copy Individual Files**")
            for fname, code in st.session_state.java_code.items():
                lang = "java" if fname.endswith(".java") else "xml"
                with st.expander(f"📄 {fname}"):
                    st.code(code, language=lang)

            st.markdown("---")
            st.markdown("### 🚀 How to Run")
            st.code("""\
# 1. Unzip the downloaded file
unzip selenium-tests.zip

# 2. Open in IntelliJ or Eclipse as a Maven project

# 3. Run all tests
mvn test

# 4. Run one specific test class
mvn -Dtest=WebAppTest test
""", language="bash")

else:
    st.info("👆 Enter a URL above and click **Start** to begin.")
