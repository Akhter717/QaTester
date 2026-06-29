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
st.set_page_config(
    page_title="Selenium Test Generator",
    page_icon="🤖",
    layout="wide"
)

st.title("🤖 AI Selenium Test Generator")
st.caption("Crawl any website → Get Test Plan → Extract Locators → Download Java Code")

# ─────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Configuration")
    groq_api_key = st.text_input("Groq API Key", type="password", placeholder="gsk_...")
    st.markdown("---")
    st.markdown("**Steps:**")
    st.markdown("1️⃣ Enter Groq API Key")
    st.markdown("2️⃣ Enter website URL")
    st.markdown("3️⃣ Click **Start**")
    st.markdown("4️⃣ Go through each tab")
    st.markdown("---")
    st.caption("Free Groq API: [console.groq.com](https://console.groq.com)")

# ─────────────────────────────────────────────
# SESSION STATE
# ─────────────────────────────────────────────
for key, default in {
    "crawled_pages": {},
    "test_plan": "",
    "locators": None,       # None = not yet extracted, [] = extracted but empty
    "java_code": {},
    "crawl_done": False
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

# ─────────────────────────────────────────────
# HELPER: BUILD LOCATORS FOR ONE ELEMENT
# Returns (css, xpath) — always returns something useful
# ─────────────────────────────────────────────
def build_locators(tag, elem):
    elem_id          = elem.get("id", "").strip()
    elem_name        = elem.get("name", "").strip()
    elem_classes     = elem.get("class", [])
    elem_type        = elem.get("type", "").strip()
    elem_placeholder = elem.get("placeholder", "").strip()
    elem_value       = elem.get("value", "").strip()
    elem_href        = elem.get("href", "").strip()
    elem_text        = elem.get_text(strip=True)[:50]
    elem_data_test   = elem.get("data-test", "").strip()
    elem_aria        = elem.get("aria-label", "").strip()
    elem_title       = elem.get("title", "").strip()

    # ── CSS Selector (priority order) ──────────
    if elem_id:
        css = f"#{elem_id}"
    elif elem_data_test:
        css = f"[data-test='{elem_data_test}']"
    elif elem_name:
        css = f"{tag}[name='{elem_name}']"
    elif elem_aria:
        css = f"[aria-label='{elem_aria}']"
    elif elem_placeholder:
        css = f"{tag}[placeholder='{elem_placeholder}']"
    elif elem_type:
        css = f"{tag}[type='{elem_type}']"
    elif elem_classes:
        # Use up to 2 classes for specificity
        cls = ".".join(elem_classes[:2])
        css = f"{tag}.{cls}"
    elif elem_value and tag == "input":
        css = f"input[value='{elem_value}']"
    else:
        css = tag  # Last resort

    # ── XPath (priority order) ──────────────────
    if elem_id:
        xpath = f"//{tag}[@id='{elem_id}']"
    elif elem_data_test:
        xpath = f"//{tag}[@data-test='{elem_data_test}']"
    elif elem_name:
        xpath = f"//{tag}[@name='{elem_name}']"
    elif elem_aria:
        xpath = f"//{tag}[@aria-label='{elem_aria}']"
    elif elem_placeholder:
        xpath = f"//{tag}[@placeholder='{elem_placeholder}']"
    elif elem_text and tag in ["button", "a", "label", "span", "h1", "h2", "h3"]:
        safe = elem_text.replace("'", "\\'")[:30]
        xpath = f"//{tag}[normalize-space()='{safe}']"
    elif elem_title:
        xpath = f"//{tag}[@title='{elem_title}']"
    elif elem_type:
        xpath = f"//{tag}[@type='{elem_type}']"
    elif elem_classes:
        xpath = f"//{tag}[contains(@class,'{elem_classes[0]}')]"
    elif elem_value and tag == "input":
        xpath = f"//input[@value='{elem_value}']"
    else:
        xpath = f"//{tag}"

    return css, xpath

# ─────────────────────────────────────────────
# HELPER: CRAWL WEBSITE
# ─────────────────────────────────────────────
def crawl_website(start_url, max_pages=15):
    visited = {}
    to_visit = [start_url]
    base_domain = urlparse(start_url).netloc

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }

    progress = st.progress(0, text="Starting crawl...")
    count = 0

    while to_visit and count < max_pages:
        url = to_visit.pop(0)
        if url in visited:
            continue
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            ct = resp.headers.get("Content-Type", "")
            if "text/html" not in ct:
                continue

            html = resp.text
            visited[url] = html
            count += 1
            progress.progress(min(count / max_pages, 1.0), text=f"Crawling ({count}): {url}")

            soup = BeautifulSoup(html, "html.parser")
            for a in soup.find_all("a", href=True):
                full = urljoin(url, a["href"])
                p = urlparse(full)
                if (
                    p.netloc == base_domain
                    and p.scheme in ["http", "https"]
                    and "#" not in full          # skip anchor-only links
                    and full not in visited
                    and full not in to_visit
                ):
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
    # All tags we care about
    tags_to_find = ["input", "button", "a", "select", "textarea",
                    "label", "checkbox", "radio"]

    seen = set()  # Avoid duplicate locators across pages

    for url, html in pages_dict.items():
        soup = BeautifulSoup(html, "html.parser")
        page_name = urlparse(url).path or "/"

        for tag in tags_to_find:
            elements = soup.find_all(tag)

            for elem in elements[:20]:  # Max 20 per tag per page
                elem_text  = elem.get_text(strip=True)[:50]
                elem_type  = elem.get("type", tag)
                elem_placeholder = elem.get("placeholder", "")

                # Build the label shown in the table
                label = (
                    elem_text
                    or elem_placeholder
                    or elem.get("aria-label", "")
                    or elem.get("name", "")
                    or elem.get("id", "")
                    or elem.get("href", "")[:30]
                    or f"<{tag}>"
                )

                css, xpath = build_locators(tag, elem)

                # Skip completely generic locators (no info at all)
                if css == tag and xpath == f"//{tag}":
                    continue

                # Skip duplicates
                dedup_key = f"{page_name}|{css}|{xpath}"
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)

                locators.append({
                    "Page": page_name,
                    "Tag": tag,
                    "Type": elem_type,
                    "Text / Label": label[:40],
                    "CSS Selector": css,
                    "XPath": xpath,
                    "_url": url   # Internal — used for code gen, not shown in table
                })

    return locators

# ─────────────────────────────────────────────
# HELPER: CALL GROQ
# ─────────────────────────────────────────────
def call_groq(api_key, prompt, system_msg="You are a QA expert."):
    client = Groq(api_key=api_key)
    resp = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user",   "content": prompt}
        ],
        temperature=0.3,
        max_tokens=4000
    )
    return resp.choices[0].message.content

# ─────────────────────────────────────────────
# HELPER: GENERATE TEST PLAN
# ─────────────────────────────────────────────
def generate_test_plan(api_key, pages_dict, locators):
    summary = ""
    for url, html in list(pages_dict.items())[:8]:
        soup  = BeautifulSoup(html, "html.parser")
        title = soup.title.string.strip() if soup.title else url
        forms    = len(soup.find_all("form"))
        buttons  = len(soup.find_all("button"))
        inputs   = len(soup.find_all("input"))
        links    = len(soup.find_all("a", href=True))
        summary += (
            f"\nPage: {url}\n"
            f"Title: {title}\n"
            f"Forms: {forms} | Buttons: {buttons} | Inputs: {inputs} | Links: {links}\n"
        )

    loc_summary = "\n".join([
        f"  - [{l['Tag']}] '{l['Text / Label']}' on {l['Page']}"
        for l in locators[:30]
    ])

    prompt = f"""
You are a senior QA engineer. Based on the website structure below, write a professional test plan.

=== PAGES FOUND ===
{summary}

=== KEY ELEMENTS FOUND ===
{loc_summary}

Write test cases covering ALL of:
1. Functional UI Testing
2. Form Validation Testing  
3. Navigation & Link Testing
4. Login/Auth Flow (if login form exists)

For each test case use this format:
---
TC_ID: TC_001
Page: <page name>
Test Description: <what is being tested>
Steps:
  1. <step>
  2. <step>
Expected Result: <what should happen>
Priority: High / Medium / Low
---

Write at least 10 test cases. Be specific to the actual pages and elements found.
"""
    return call_groq(api_key, prompt, "You are a senior QA engineer writing a professional test plan.")

# ─────────────────────────────────────────────
# HELPER: GENERATE JAVA CODE
# ─────────────────────────────────────────────
def generate_java_code(api_key, locators, pages_dict):
    java_files = {}

    # Group locators by page path
    pages = {}
    for loc in locators[:50]:
        page = loc["Page"].strip("/").replace("/", "_").replace("-", "_") or "home"
        pages.setdefault(page, []).append(loc)

    # Generate one Page Object class per page (max 4)
    for page_name, page_locs in list(pages.items())[:4]:
        class_name = (
            "".join(w.capitalize() for w in re.split(r"[_\-\s]+", page_name) if w)
            + "Page"
        )

        elements_info = "\n".join([
            f'  Element: [{l["Tag"]}] label="{l["Text / Label"]}" '
            f'CSS="{l["CSS Selector"]}" XPath="{l["XPath"]}"'
            for l in page_locs[:12]
        ])

        prompt = f"""
Generate a complete Selenium Java Page Object Model class named {class_name}.

Elements to include:
{elements_info}

Requirements:
- Package: pages
- Use @FindBy annotations — prefer CSS selector, use XPath as fallback
- Constructor receives WebDriver and calls PageFactory.initElements(driver, this)
- Add one action method per element (click for buttons/links, sendKeys for inputs, getText for text elements)
- Return type of methods: void for actions, String for getText
- Include ALL imports needed (org.openqa.selenium.*, org.openqa.selenium.support.FindBy, org.openqa.selenium.support.PageFactory)
- Return ONLY valid Java code. No explanation. No markdown fences.
"""
        code = call_groq(api_key, prompt, "You are a Selenium Java expert. Return only clean compilable Java code without markdown.")
        code = re.sub(r"```(?:java)?|```", "", code).strip()
        java_files[f"src/main/java/pages/{class_name}.java"] = code

    # Generate TestNG test class
    page_classes = [
        "".join(w.capitalize() for w in re.split(r"[_\-\s]+", p) if w) + "Page"
        for p in list(pages.keys())[:4]
    ]

    sample_locs = "\n".join([
        f'  [{l["Tag"]}] "{l["Text / Label"]}" — CSS: {l["CSS Selector"]}'
        for l in locators[:20]
    ])

    test_prompt = f"""
Generate a complete Selenium Java TestNG test class named WebAppTest.

Page Object classes available: {", ".join(page_classes)}

Key elements available:
{sample_locs}

Requirements:
- Package: tests
- @BeforeClass: setup ChromeDriver using WebDriverManager (io.github.bonigarcia.wdm.WebDriverManager)
- @AfterClass: quit driver
- Write exactly 6 @Test methods:
    1. testPageTitle() — verify page title is not empty
    2. testNavigationLinks() — click nav links and verify page loads
    3. testFormInputs() — fill and submit any form found
    4. testButtonClicks() — click available buttons, verify no crash
    5. testDataDriven(@DataProvider) — run one form test with 2 data sets
    6. testPageElements() — assert key elements are displayed
- Use Assert class for all assertions
- Import all needed classes
- Return ONLY valid Java code. No explanation. No markdown fences.
"""
    test_code = call_groq(api_key, test_prompt, "You are a Selenium Java TestNG expert. Return only clean compilable Java code without markdown.")
    test_code = re.sub(r"```(?:java)?|```", "", test_code).strip()
    java_files["src/test/java/tests/WebAppTest.java"] = test_code

    # pom.xml
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
col1, col2 = st.columns([3, 1])
with col1:
    url_input = st.text_input(
        "Website URL",
        placeholder="https://demoqa.com  or  https://the-internet.herokuapp.com",
        label_visibility="collapsed"
    )
with col2:
    start_btn = st.button("🚀 Start", use_container_width=True, type="primary")

if start_btn:
    if not groq_api_key:
        st.error("❌ Please enter your Groq API Key in the sidebar.")
        st.stop()
    if not url_input or not url_input.startswith("http"):
        st.error("❌ Please enter a valid URL starting with http:// or https://")
        st.stop()

    # Reset all state
    st.session_state.crawled_pages = {}
    st.session_state.test_plan     = ""
    st.session_state.locators      = None
    st.session_state.java_code     = {}
    st.session_state.crawl_done    = False

    with st.spinner("🔍 Crawling website..."):
        pages = crawl_website(url_input, max_pages=15)
        st.session_state.crawled_pages = pages
        st.session_state.crawl_done    = True

    st.success(f"✅ Crawl complete! Found **{len(pages)} pages**. Continue in the tabs below.")

# ─────────────────────────────────────────────
# TABS
# ─────────────────────────────────────────────
if st.session_state.crawl_done:
    tab1, tab2, tab3, tab4 = st.tabs([
        "📝 Step 1: Test Plan",
        "🎯 Step 2: Locators",
        "☕ Step 3: Java Code",
        "📥 Step 4: Download"
    ])

    # ── TAB 1: TEST PLAN ────────────────────────
    with tab1:
        st.subheader("📝 AI-Generated Test Plan")
        st.caption("AI analyses the crawled pages and writes a full QA test plan.")

        with st.expander("📋 Crawled Pages"):
            for i, u in enumerate(st.session_state.crawled_pages.keys(), 1):
                st.write(f"{i}. {u}")

        if st.session_state.test_plan:
            st.markdown(st.session_state.test_plan)
            if st.button("🔄 Regenerate Test Plan"):
                st.session_state.test_plan = ""
                st.rerun()
        else:
            if st.button("🧠 Generate Test Plan", type="primary"):
                with st.spinner("AI is writing your test plan..."):
                    plan = generate_test_plan(
                        groq_api_key,
                        st.session_state.crawled_pages,
                        st.session_state.locators or []
                    )
                    st.session_state.test_plan = plan
                st.rerun()

    # ── TAB 2: LOCATORS ─────────────────────────
    with tab2:
        st.subheader("🎯 Element Locators — XPath & CSS")
        st.caption("Extracted from crawled HTML. Both XPath and CSS provided for every element.")

        # Always show the Extract button so user can re-run
        col_btn, col_info = st.columns([1, 3])
        with col_btn:
            extract_btn = st.button("🔍 Extract Locators", type="primary", use_container_width=True)

        if extract_btn:
            with st.spinner("Scanning all pages for interactive elements..."):
                locs = extract_locators(st.session_state.crawled_pages)
                st.session_state.locators = locs   # Could be empty list — that's fine

        # Show results if extraction has been run
        if st.session_state.locators is not None:
            locs = st.session_state.locators
            if len(locs) == 0:
                st.warning(
                    "⚠️ No locators found. This usually means the site loads content "
                    "via JavaScript (React/Angular). Try **https://the-internet.herokuapp.com** "
                    "or **https://demoqa.com/text-box** instead."
                )
            else:
                st.success(f"✅ Found **{len(locs)} elements** across all pages.")

                df = pd.DataFrame(locs)

                # Page filter
                page_options = ["All Pages"] + sorted(df["Page"].unique().tolist())
                selected = st.selectbox("Filter by Page", page_options)
                filtered = df if selected == "All Pages" else df[df["Page"] == selected]

                # Show table — hide internal _url column
                display_cols = ["Page", "Tag", "Type", "Text / Label", "CSS Selector", "XPath"]
                st.dataframe(filtered[display_cols], use_container_width=True, height=400)
                st.caption(f"Showing {len(filtered)} of {len(locs)} total elements")

    # ── TAB 3: JAVA CODE ────────────────────────
    with tab3:
        st.subheader("☕ Generated Java Code")
        st.caption("Page Object Model classes + TestNG Data-Driven test class.")

        if st.session_state.locators is None:
            st.warning("⚠️ Go to Step 2 and click **Extract Locators** first.")
        elif len(st.session_state.locators) == 0:
            st.warning("⚠️ No locators found. Cannot generate code without elements.")
        elif st.session_state.java_code:
            for filename, code in st.session_state.java_code.items():
                lang = "java" if filename.endswith(".java") else "xml"
                with st.expander(f"📄 {filename}"):
                    st.code(code, language=lang)
            if st.button("🔄 Regenerate Code"):
                st.session_state.java_code = {}
                st.rerun()
        else:
            st.info(f"Ready to generate code from **{len(st.session_state.locators)} elements**.")
            if st.button("⚙️ Generate Java Code", type="primary"):
                with st.spinner("AI is generating Selenium Java code... (~30 seconds)"):
                    java_files = generate_java_code(
                        groq_api_key,
                        st.session_state.locators,
                        st.session_state.crawled_pages
                    )
                    st.session_state.java_code = java_files
                st.rerun()

    # ── TAB 4: DOWNLOAD ─────────────────────────
    with tab4:
        st.subheader("📥 Download Your Test Suite")

        if not st.session_state.java_code:
            st.warning("⚠️ Please generate Java code in Step 3 first.")
        else:
            st.success(f"✅ {len(st.session_state.java_code)} files ready.")

            c1, c2 = st.columns(2)
            with c1:
                zip_data = create_zip(st.session_state.java_code)
                st.download_button(
                    label="📦 Download Full Maven Project (.zip)",
                    data=zip_data,
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
            for filename, code in st.session_state.java_code.items():
                lang = "java" if filename.endswith(".java") else "xml"
                with st.expander(f"📄 {filename}"):
                    st.code(code, language=lang)

            st.markdown("---")
            st.markdown("### 🚀 How to Run")
            st.code("""\
# 1. Unzip
unzip selenium-tests.zip

# 2. Open in IntelliJ or Eclipse as Maven project

# 3. Run all tests
mvn test

# 4. Run one specific test
mvn -Dtest=WebAppTest test
""", language="bash")

else:
    st.info("👆 Enter a URL above and click **Start** to begin.")
