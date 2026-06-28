import streamlit as st
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import zipfile
import io
import json
import re
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
# SIDEBAR — API KEY INPUT
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
# SESSION STATE — stores data across tabs
# ─────────────────────────────────────────────
if "crawled_pages" not in st.session_state:
    st.session_state.crawled_pages = {}   # {url: html_content}
if "test_plan" not in st.session_state:
    st.session_state.test_plan = ""
if "locators" not in st.session_state:
    st.session_state.locators = []        # list of dicts
if "java_code" not in st.session_state:
    st.session_state.java_code = {}       # {filename: code}
if "crawl_done" not in st.session_state:
    st.session_state.crawl_done = False

# ─────────────────────────────────────────────
# HELPER: CRAWL WEBSITE
# ─────────────────────────────────────────────
def crawl_website(start_url, max_pages=20):
    """
    Crawls the given URL and all internal links.
    Returns a dict of {url: html_text}.
    Stops after max_pages to avoid infinite loops.
    """
    visited = {}
    to_visit = [start_url]
    base_domain = urlparse(start_url).netloc

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    progress = st.progress(0, text="Starting crawl...")
    count = 0

    while to_visit and count < max_pages:
        url = to_visit.pop(0)

        if url in visited:
            continue

        try:
            response = requests.get(url, headers=headers, timeout=8)
            if "text/html" not in response.headers.get("Content-Type", ""):
                continue

            html = response.text
            visited[url] = html

            count += 1
            progress.progress(min(count / max_pages, 1.0), text=f"Crawling: {url}")

            # Find all internal links
            soup = BeautifulSoup(html, "html.parser")
            for a_tag in soup.find_all("a", href=True):
                full_url = urljoin(url, a_tag["href"])
                parsed = urlparse(full_url)
                # Only follow links on the same domain, skip anchors/mailto
                if (parsed.netloc == base_domain
                        and full_url not in visited
                        and full_url not in to_visit
                        and parsed.scheme in ["http", "https"]):
                    to_visit.append(full_url)

        except Exception:
            # Skip pages that fail to load
            continue

    progress.empty()
    return visited

# ─────────────────────────────────────────────
# HELPER: EXTRACT LOCATORS FROM HTML
# ─────────────────────────────────────────────
def extract_locators(pages_dict):
    """
    Parses HTML of each page and extracts interactive elements.
    Returns a list of dicts with element info + XPath + CSS locator.
    """
    locators = []
    tags_to_find = ["input", "button", "a", "select", "textarea"]

    for url, html in pages_dict.items():
        soup = BeautifulSoup(html, "html.parser")
        page_name = urlparse(url).path or "/"

        for tag in tags_to_find:
            for elem in soup.find_all(tag)[:15]:  # Max 15 elements per tag per page

                elem_id    = elem.get("id", "")
                elem_name  = elem.get("name", "")
                elem_class = " ".join(elem.get("class", []))
                elem_type  = elem.get("type", tag)
                elem_text  = elem.get_text(strip=True)[:40]
                elem_href  = elem.get("href", "")
                elem_placeholder = elem.get("placeholder", "")

                # ── Build CSS Selector ──────────────────
                if elem_id:
                    css = f"#{elem_id}"
                elif elem_name:
                    css = f"{tag}[name='{elem_name}']"
                elif elem_class:
                    first_class = elem.get("class", [""])[0]
                    css = f"{tag}.{first_class}"
                elif elem_type != tag:
                    css = f"{tag}[type='{elem_type}']"
                else:
                    css = tag

                # ── Build XPath ─────────────────────────
                if elem_id:
                    xpath = f"//{tag}[@id='{elem_id}']"
                elif elem_name:
                    xpath = f"//{tag}[@name='{elem_name}']"
                elif elem_text and tag in ["button", "a"]:
                    safe_text = elem_text.replace("'", "\\'")
                    xpath = f"//{tag}[contains(text(),'{safe_text}')]"
                elif elem_placeholder:
                    xpath = f"//{tag}[@placeholder='{elem_placeholder}']"
                elif elem_class:
                    first_class = elem.get("class", [""])[0]
                    xpath = f"//{tag}[contains(@class,'{first_class}')]"
                else:
                    xpath = f"//{tag}"

                # Skip elements with no useful locator info
                if css == tag and xpath == f"//{tag}":
                    continue

                locators.append({
                    "Page": page_name,
                    "Tag": tag,
                    "Type": elem_type,
                    "Text/Label": elem_text or elem_placeholder or elem_href[:30],
                    "CSS Selector": css,
                    "XPath": xpath,
                    "URL": url
                })

    return locators

# ─────────────────────────────────────────────
# HELPER: CALL GROQ API
# ─────────────────────────────────────────────
def call_groq(api_key, prompt, system_msg="You are a QA expert."):
    """Simple wrapper to call Groq LLM."""
    client = Groq(api_key=api_key)
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": prompt}
        ],
        temperature=0.3,
        max_tokens=4000
    )
    return response.choices[0].message.content

# ─────────────────────────────────────────────
# HELPER: GENERATE TEST PLAN
# ─────────────────────────────────────────────
def generate_test_plan(api_key, pages_dict):
    """Sends crawled page info to Groq and gets a test plan."""
    # Build a summary of pages and their elements
    summary = ""
    for url, html in list(pages_dict.items())[:10]:  # Limit to 10 pages for prompt size
        soup = BeautifulSoup(html, "html.parser")
        title = soup.title.string if soup.title else url
        forms = len(soup.find_all("form"))
        buttons = len(soup.find_all("button"))
        inputs = len(soup.find_all("input"))
        links = len(soup.find_all("a", href=True))
        summary += f"\nPage: {url}\nTitle: {title}\nForms: {forms}, Buttons: {buttons}, Inputs: {inputs}, Links: {links}\n"

    prompt = f"""
You are a senior QA engineer. Based on the following website structure, create a detailed test plan.

Website pages found:
{summary}

Create test cases covering:
1. Functional UI Testing
2. Form Validation Testing
3. Navigation & Link Testing
4. Login/Auth Flow Testing (if applicable)

For each test case, provide:
- Test Case ID (TC_001, TC_002 etc.)
- Page Name
- Test Description
- Test Steps (numbered)
- Expected Result
- Priority (High/Medium/Low)

Format it clearly so a team can understand and present it.
"""
    return call_groq(api_key, prompt, "You are a senior QA engineer writing professional test plans.")

# ─────────────────────────────────────────────
# HELPER: GENERATE JAVA CODE
# ─────────────────────────────────────────────
def generate_java_code(api_key, locators, pages_dict):
    """Generates POM + TestNG Java code using Groq."""

    # Group locators by page
    pages = {}
    for loc in locators[:40]:  # Limit for prompt size
        page = loc["Page"].strip("/").replace("/", "_").replace("-", "_") or "home"
        if page not in pages:
            pages[page] = []
        pages[page].append(loc)

    java_files = {}

    # Generate one Page Object class per page
    for page_name, page_locs in list(pages.items())[:5]:  # Max 5 pages
        class_name = "".join(word.capitalize() for word in re.split(r"[_\-]", page_name)) + "Page"

        locator_lines = "\n".join([
            f'  // {l["Text/Label"]} — CSS: {l["CSS Selector"]}'
            for l in page_locs[:10]
        ])

        prompt = f"""
Generate a Selenium Java Page Object Model class named {class_name} for page "{page_name}".

Elements on this page:
{locator_lines}

Rules:
- Use @FindBy annotations with the CSS or XPath selectors shown
- Use PageFactory.initElements in constructor
- Add a simple method for each element (click, sendKeys, getText)
- Driver should be passed in constructor
- Include all necessary imports (org.openqa.selenium, org.openqa.selenium.support)
- Return ONLY the Java code, no explanation

Page URL context: one of {list(pages_dict.keys())[:3]}
"""
        code = call_groq(api_key, prompt, "You are a Selenium Java expert. Return only clean Java code.")
        # Strip markdown code fences if present
        code = re.sub(r"```java|```", "", code).strip()
        java_files[f"src/main/java/pages/{class_name}.java"] = code

    # Generate TestNG test class
    page_list = ", ".join([
        "".join(word.capitalize() for word in re.split(r"[_\-]", p)) + "Page"
        for p in list(pages.keys())[:5]
    ])

    test_prompt = f"""
Generate a Selenium Java TestNG test class named "WebAppTest" that tests the following Page Object classes: {page_list}.

Rules:
- Use @Test, @BeforeClass, @AfterClass, @DataProvider annotations
- Use @DataProvider for data-driven testing with at least one test
- Initialize ChromeDriver in @BeforeClass
- Quit driver in @AfterClass
- Write at least 5 meaningful test methods covering: navigation, form input, button clicks, link validation
- Include assertions using Assert class
- Include all necessary imports
- Return ONLY the Java code, no explanation
"""
    test_code = call_groq(api_key, test_prompt, "You are a Selenium Java TestNG expert. Return only clean Java code.")
    test_code = re.sub(r"```java|```", "", test_code).strip()
    java_files["src/test/java/tests/WebAppTest.java"] = test_code

    # Add pom.xml
    java_files["pom.xml"] = """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>

    <groupId>com.selenium.tests</groupId>
    <artifactId>ai-generated-tests</artifactId>
    <version>1.0-SNAPSHOT</version>

    <dependencies>
        <!-- Selenium -->
        <dependency>
            <groupId>org.seleniumhq.selenium</groupId>
            <artifactId>selenium-java</artifactId>
            <version>4.18.1</version>
        </dependency>

        <!-- TestNG -->
        <dependency>
            <groupId>org.testng</groupId>
            <artifactId>testng</artifactId>
            <version>7.9.0</version>
            <scope>test</scope>
        </dependency>

        <!-- WebDriverManager (auto-download ChromeDriver) -->
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
# HELPER: CREATE ZIP FILE
# ─────────────────────────────────────────────
def create_zip(java_files):
    """Packages all Java files into a downloadable ZIP."""
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for filepath, content in java_files.items():
            zf.writestr(filepath, content)
    zip_buffer.seek(0)
    return zip_buffer

# ─────────────────────────────────────────────
# MAIN UI — URL INPUT
# ─────────────────────────────────────────────
st.markdown("### 🌐 Enter Website URL")
col1, col2 = st.columns([3, 1])
with col1:
    url_input = st.text_input("Website URL", placeholder="https://example.com", label_visibility="collapsed")
with col2:
    start_btn = st.button("🚀 Start", use_container_width=True, type="primary")

if start_btn:
    if not groq_api_key:
        st.error("❌ Please enter your Groq API Key in the sidebar.")
        st.stop()
    if not url_input or not url_input.startswith("http"):
        st.error("❌ Please enter a valid URL starting with http:// or https://")
        st.stop()

    # Reset state
    st.session_state.crawled_pages = {}
    st.session_state.test_plan = ""
    st.session_state.locators = []
    st.session_state.java_code = {}
    st.session_state.crawl_done = False

    with st.spinner("🔍 Crawling website... This may take a moment."):
        pages = crawl_website(url_input, max_pages=15)
        st.session_state.crawled_pages = pages
        st.session_state.crawl_done = True

    st.success(f"✅ Crawl complete! Found **{len(pages)} pages**. Now go through the tabs below.")

# ─────────────────────────────────────────────
# TABS — Only show after crawl
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
        st.caption("The AI analyzes your crawled pages and creates a full QA test plan.")

        if st.session_state.test_plan:
            st.markdown(st.session_state.test_plan)
        else:
            if st.button("🧠 Generate Test Plan", type="primary"):
                with st.spinner("AI is writing your test plan..."):
                    plan = generate_test_plan(groq_api_key, st.session_state.crawled_pages)
                    st.session_state.test_plan = plan
                st.success("✅ Test plan ready!")
                st.rerun()

        # Show crawled pages summary
        with st.expander("📋 View Crawled Pages"):
            for i, url in enumerate(st.session_state.crawled_pages.keys(), 1):
                st.write(f"{i}. {url}")

    # ── TAB 2: LOCATORS ─────────────────────────
    with tab2:
        st.subheader("🎯 Element Locators (XPath + CSS)")
        st.caption("Extracted from every page — ready to use in your Page Object classes.")

        if st.session_state.locators:
            import pandas as pd
            df = pd.DataFrame(st.session_state.locators)
            # Filter by page
            pages_list = ["All Pages"] + sorted(df["Page"].unique().tolist())
            selected_page = st.selectbox("Filter by Page", pages_list)
            if selected_page != "All Pages":
                df = df[df["Page"] == selected_page]
            st.dataframe(df[["Page", "Tag", "Text/Label", "CSS Selector", "XPath"]], use_container_width=True)
            st.caption(f"Total elements found: {len(df)}")
        else:
            if st.button("🔍 Extract Locators", type="primary"):
                with st.spinner("Extracting XPath and CSS selectors..."):
                    locs = extract_locators(st.session_state.crawled_pages)
                    st.session_state.locators = locs
                st.success(f"✅ Found {len(locs)} elements!")
                st.rerun()

    # ── TAB 3: JAVA CODE ────────────────────────
    with tab3:
        st.subheader("☕ Generated Java Code")
        st.caption("POM Page Object classes + TestNG Data-Driven test class.")

        if not st.session_state.locators:
            st.warning("⚠️ Please extract locators first in Step 2.")
        elif st.session_state.java_code:
            for filename, code in st.session_state.java_code.items():
                with st.expander(f"📄 {filename}"):
                    st.code(code, language="java" if filename.endswith(".java") else "xml")
        else:
            if st.button("⚙️ Generate Java Code", type="primary"):
                with st.spinner("AI is generating your Selenium Java code... (may take ~30 seconds)"):
                    java_files = generate_java_code(
                        groq_api_key,
                        st.session_state.locators,
                        st.session_state.crawled_pages
                    )
                    st.session_state.java_code = java_files
                st.success(f"✅ Generated {len(java_files)} files!")
                st.rerun()

    # ── TAB 4: DOWNLOAD ─────────────────────────
    with tab4:
        st.subheader("📥 Download Your Test Suite")
        st.caption("All generated files packaged and ready to import into your IDE.")

        if not st.session_state.java_code:
            st.warning("⚠️ Please generate Java code first in Step 3.")
        else:
            st.success(f"✅ {len(st.session_state.java_code)} files ready for download")

            col1, col2 = st.columns(2)

            with col1:
                # Download as ZIP
                zip_data = create_zip(st.session_state.java_code)
                st.download_button(
                    label="📦 Download as .zip (Full Maven Project)",
                    data=zip_data,
                    file_name="selenium-tests.zip",
                    mime="application/zip",
                    use_container_width=True,
                    type="primary"
                )

            with col2:
                # Download test class only as .java
                test_file_content = ""
                for fname, code in st.session_state.java_code.items():
                    if "WebAppTest.java" in fname:
                        test_file_content = code
                        break

                if test_file_content:
                    st.download_button(
                        label="☕ Download WebAppTest.java only",
                        data=test_file_content,
                        file_name="WebAppTest.java",
                        mime="text/plain",
                        use_container_width=True
                    )

            # Copy to clipboard — show all files
            st.markdown("---")
            st.markdown("**📋 Copy Individual Files**")
            for filename, code in st.session_state.java_code.items():
                with st.expander(f"Copy: {filename}"):
                    st.code(code, language="java" if filename.endswith(".java") else "xml")

            # Show Maven instructions
            st.markdown("---")
            st.markdown("### 🚀 How to Run These Tests")
            st.code("""
# 1. Unzip the downloaded file
unzip selenium-tests.zip

# 2. Open in IntelliJ IDEA or Eclipse as Maven project

# 3. Run all tests
mvn test

# 4. Run specific test
mvn -Dtest=WebAppTest test
            """, language="bash")

else:
    st.info("👆 Enter a URL above and click **Start** to begin.")
