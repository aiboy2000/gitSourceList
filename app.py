from flask import Flask, render_template, request
import requests
import re # For regex matching of file patterns
import os # For API Key and other environment variables
from dotenv import load_dotenv # For loading .env file

app = Flask(__name__)

load_dotenv() # Load variables from .env file into environment

from database import init_db, SessionLocal, AnalyzedFile, FeatureSynthesis # Import database components and models

import google.generativeai as genai # For Gemini API

# Now, os.getenv will be able to pick up GEMINI_API_KEY if it's in the .env file or already in the environment
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
else:
    print("Warning: GEMINI_API_KEY not found. AI analysis will be disabled.")

# We will remove UNNECESSARY_FILE_PATTERNS and suggest_files_to_ignore

@app.route('/', methods=['GET', 'POST'])
def index():
    error = None
    repo_url = request.form.get('repo_url') if request.method == 'POST' else request.args.get('repo_url', '')
    pat = request.form.get('pat') if request.method == 'POST' else request.args.get('pat', '')
    branches = []

    if request.method == 'POST' and 'fetch_branches' in request.form:
        if not repo_url:
            error = "リポジトリURLが必要です。"
        else:
            try:
                parts = repo_url.strip('/').split('/')
                if len(parts) < 2 or parts[-2] == '' or parts[-1] == '':
                    raise ValueError("無効なGitHubリポジトリURL形式です。")

                user, repo = parts[-2], parts[-1]
                api_url = f"https://api.github.com/repos/{user}/{repo}/branches"

                headers = {'Accept': 'application/vnd.github.v3+json'}
                if pat:
                    headers['Authorization'] = f'token {pat}'

                response = requests.get(api_url, headers=headers)
                response.raise_for_status()
                branches_data = response.json()

                if not branches_data:
                    error = "ブランチが見つかりません。リポジトリが空であるか、URLが無効であるか、プライベートリポジトリのトークンに権限がない可能性があります。"

                for branch_data in branches_data:
                    branches.append({
                        'name': branch_data['name'],
                        'sha': branch_data['commit']['sha']
                    })

            except ValueError as ve:
                error = str(ve)
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 404:
                    error = "リポジトリが見つかりません。URLを確認してください。プライベートの場合は、PATが有効で 'repo' スコープがあることを確認してください。"
                elif e.response.status_code == 401:
                    error = "認証に失敗しました。提供されたPATが無効であるか、期限切れの可能性があります。"
                elif e.response.status_code == 403:
                     error = "アクセスが禁止されています。PATに必要な権限がない (例: 'repo' スコープ) か、レート制限に達した可能性があります。"
                else:
                    error = f"ブランチの取得中にエラーが発生しました ({e.response.status_code}): {e}"
            except requests.exceptions.RequestException as e:
                error = f"ブランチ取得中のネットワークエラー: {e}"
            except Exception as e:
                error = f"予期せぬエラーが発生しました: {e}"

    return render_template('index.html', error=error, repo_url=repo_url, branches=branches, pat=pat)


@app.route('/commits_for_branch', methods=['GET', 'POST'])
def commits_for_branch():
    pat = ''
    if request.method == 'POST':
        repo_url = request.form.get('repo_url')
        branch_name = request.form.get('branch_name')
        pat = request.form.get('pat')
    else: # GET request
        repo_url = request.args.get('repo_url')
        branch_name = request.args.get('branch_name')
        pat = request.args.get('pat')

    commits = []
    error = None

    if not repo_url or not branch_name:
        error = "リポジトリURLとブランチ名が必要です。"
        return render_template('index.html', error=error, repo_url=repo_url, branches=[], pat=pat)

    try:
        parts = repo_url.strip('/').split('/')
        user, repo = parts[-2], parts[-1]
        api_url = f"https://api.github.com/repos/{user}/{repo}/commits?sha={branch_name}"

        headers = {'Accept': 'application/vnd.github.v3+json'}
        if pat:
            headers['Authorization'] = f'token {pat}'


        headers = {'Accept': 'application/vnd.github.v3+json'}
        if pat:
            headers['Authorization'] = f'token {pat}'

        # Max commits to fetch to prevent extremely long loads
        MAX_COMMITS_TO_FETCH = 500
        page_url = api_url # Start with the first page URL
        commit_count_status_message = ""

        while page_url and len(commits) < MAX_COMMITS_TO_FETCH:
            response = requests.get(page_url, headers=headers)
            response.raise_for_status()
            current_page_commits_data = response.json()

            if not current_page_commits_data: # No more commits on this page or empty response
                break

            for commit_data in current_page_commits_data:
                if len(commits) >= MAX_COMMITS_TO_FETCH:
                    commit_count_status_message = f"表示するコミットが多すぎるため、最新{MAX_COMMITS_TO_FETCH}件のみ表示しています。"
                    break
                commits.append({
                    'sha': commit_data['sha'],
                    'message': commit_data['commit']['message'].splitlines()[0],
                    'author': commit_data['commit']['author']['name'],
                    'date': commit_data['commit']['author']['date']
                })

            if len(commits) >= MAX_COMMITS_TO_FETCH: # Check again after appending
                break

            # Get next page URL from Link header
            if 'Link' in response.headers:
                links = requests.utils.parse_header_links(response.headers['Link'])
                next_url = None
                for link in links:
                    if link.get('rel') == 'next':
                        next_url = link.get('url')
                        break
                page_url = next_url
            else: # No Link header, means no more pages
                page_url = None

        if not commits and not error : # If after all pagination, still no commits
             error = f"ブランチ '{branch_name}' にコミットが見つかりませんでした。"


    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            error = f"ブランチ '{branch_name}' のコミットが見つかりません。リポジトリ/ブランチを確認してください。プライベートの場合は、PATが有効であることを確認してください。"
        elif e.response.status_code == 401:
            error = "コミット取得時の認証に失敗しました。PATが無効である可能性があります。"
        elif e.response.status_code == 403:
            error = "コミット取得時のアクセスが禁止されています。PATに権限がないか、レート制限に達した可能性があります。"
        else:
            error = f"コミット取得エラー ({e.response.status_code}): {e}"
    except requests.exceptions.RequestException as e:
        error = f"コミット取得時のネットワークエラー: {e}"
    except Exception as e:
        error = f"予期せぬエラーが発生しました: {e}"

    fetched_branches = []
    if repo_url:
        try:
            parts_b = repo_url.strip('/').split('/')
            user_b, repo_b = parts_b[-2], parts_b[-1]
            api_url_b = f"https://api.github.com/repos/{user_b}/{repo_b}/branches"
            headers_b = {'Accept': 'application/vnd.github.v3+json'}
            if pat:
                headers_b['Authorization'] = f'token {pat}'
            response_b = requests.get(api_url_b, headers=headers_b)
            response_b.raise_for_status()
            for branch_data in response_b.json():
                fetched_branches.append({
                    'name': branch_data['name'],
                    'sha': branch_data['commit']['sha']
                })
        except Exception as e_b:
            print(f"commits_for_branchでのブランチ再取得エラー: {e_b}")
            if not error:
                 error = "ブランチセレクタを表示するためにブランチを再取得できませんでした。コミットリストは正確な場合があります。"


    return render_template('index.html',
                           repo_url=repo_url,
                           selected_branch_name=branch_name,
                           commits=commits,
                           branches=fetched_branches,
                           error=error,
                           pat=pat,
                           commit_count_status_message=commit_count_status_message)


@app.route('/select_commit', methods=['GET', 'POST'])
def select_commit():
    branch_name = None
    pat = ''
    if request.method == 'POST':
        repo_url = request.form.get('repo_url')
        commit_sha = request.form.get('commit_sha')
        branch_name = request.form.get('branch_name')
        pat = request.form.get('pat')
    else: # GET request
        repo_url = request.args.get('repo_url')
        commit_sha = request.args.get('commit_sha')
        branch_name = request.args.get('branch_name')
        pat = request.args.get('pat')

    files = []
    error = None

    if not repo_url or not commit_sha:
        error = "リポジトリURLまたはコミットSHAがありません。"
        return render_template('index.html', error=error, repo_url=repo_url, selected_branch_name=branch_name, pat=pat)

    try:
        parts = repo_url.strip('/').split('/')
        if len(parts) < 2:
            raise ValueError("無効なGitHubリポジトリURL形式です。")
        user, repo = parts[-2], parts[-1]

        api_url = f"https://api.github.com/repos/{user}/{repo}/commits/{commit_sha}"
        headers = {'Accept': 'application/vnd.github.v3+json'}
        if pat:
            headers['Authorization'] = f'token {pat}'

        response = requests.get(api_url, headers=headers) # Get basic commit data first
        response.raise_for_status()
        commit_data = response.json()

        if 'commit' in commit_data and 'tree' in commit_data['commit']:
            tree_sha = commit_data['commit']['tree']['sha']
            tree_api_url = f"https://api.github.com/repos/{user}/{repo}/git/trees/{tree_sha}?recursive=1"

            print(f"Fetching full tree: {tree_api_url}") # For debugging
            tree_response = requests.get(tree_api_url, headers=headers)
            tree_response.raise_for_status()
            tree_data = tree_response.json()

            if 'tree' in tree_data:
                for item in tree_data['tree']:
                    if item['type'] == 'blob':  # Ensure it's a file, not a directory or submodule
                        files.append({
                            'filename': item['path'],
                            'status': 'tree' # Indicate it's from the full tree listing
                                             # Actual status (added, modified) isn't available from tree directly
                                             # but this distinguishes from the old 'files' array if we ever combined.
                        })
            else:
                error = "リポジトリツリーの取得に成功しましたが、ツリーデータが空です。"

            if not files and not error: # If tree was fetched but no files found (e.g. empty repo at commit)
                error = "このコミットにはファイルが見つかりませんでした (ツリー表示)。"

        else:
            error = "コミットデータからツリー情報を取得できませんでした。"
            # Fallback or alternative: try to use commit_data['files'] if primary method fails?
            # For now, strict full tree. If 'files' (changed files) is desired, it's a different logic path.
            # The goal here is to list ALL files at that commit state.

    except ValueError as ve:
        error = str(ve)
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            error = "コミット詳細が見つかりません。URL/SHAを確認してください。プライベートの場合は、PATが有効であることを確認してください。"
        elif e.response.status_code == 401:
            error = "コミット詳細取得時の認証に失敗しました。PATが無効である可能性があります。"
        elif e.response.status_code == 403:
            error = "コミット詳細取得時のアクセスが禁止されています。PATに権限がないか、レート制限に達した可能性があります。"
        else:
            error = f"コミット詳細取得エラー ({e.response.status_code}): {e}"
    except requests.exceptions.RequestException as e:
        error = f"コミット詳細取得時のネットワークエラー: {e}"
    except Exception as e:
        error = f"ファイル取得中に予期せぬエラーが発生しました: {e}"

    return render_template('commit_files.html',
                           repo_url=repo_url,
                           commit_sha=commit_sha,
                           files=files,
                           error=error,
                           branch_name=branch_name,
                           pat=pat)

# The /process_files route and its associated logic for .gitignore and git filter-repo are removed.
# A new route /analyze_file_role will be added next.

import base64 # For decoding file content from GitHub API

@app.route('/analyze_file_role', methods=['GET']) # Using GET for simplicity, could be POST
def analyze_file_role():
    repo_url = request.args.get('repo_url')
    commit_sha = request.args.get('commit_sha') # Or use branch_name if analyzing latest
    file_path = request.args.get('file_path')
    pat = request.args.get('pat')
    branch_name = request.args.get('branch_name') # Keep for context

    error = None
    analysis_result = "分析はまだ実行されていません。" # Default message in Japanese
    file_content = None

    if not all([repo_url, commit_sha, file_path]):
        error = "リポジトリURL、コミットSHA、ファイルパスが必要です。"
        # Redirect or render with error:
        return render_template('commit_files.html', # Or a dedicated error page or back to index
                               error=error,
                               repo_url=repo_url,
                               commit_sha=commit_sha,
                               branch_name=branch_name,
                               pat=pat,
                               files=[]) # May need to repopulate files if redirecting to commit_files

    try:
        parts = repo_url.strip('/').split('/')
        user, repo = parts[-2], parts[-1]
        # Construct URL to get file content at a specific commit
        # Using ref=commit_sha ensures we get the version from that commit
        api_url = f"https://api.github.com/repos/{user}/{repo}/contents/{file_path}?ref={commit_sha}"

        headers = {'Accept': 'application/vnd.github.v3+json'}
        if pat:
            headers['Authorization'] = f'token {pat}'

        response = requests.get(api_url, headers=headers)
        response.raise_for_status()
        file_data = response.json()

        if file_data.get('type') != 'file':
            error = f"指定されたパス '{file_path}' はファイルではありません。"
        elif 'content' not in file_data:
            error = f"ファイル '{file_path}' のコンテンツを取得できませんでした。エンコーディングに問題があるか、空のファイルの可能性があります。"
        else:
            file_content_encoded = file_data['content']
            file_content_bytes = base64.b64decode(file_content_encoded)
            try:
                file_content = file_content_bytes.decode('utf-8')
            except UnicodeDecodeError:
                # Attempt fallback or inform user about non-UTF-8 content
                file_content = file_content_bytes.decode('latin-1', errors='replace')
                analysis_result = "ファイルはUTF-8でデコードできませんでした。コンテンツは代替エンコーディングで表示されています。AI分析の品質に影響する可能性があります。"


            if file_content and GEMINI_API_KEY:
                try:
                    # Retrieve model name from env, with a default
                    model_name_from_env = os.getenv("GEMINI_MODEL_NAME", "gemini-pro")
                    model = genai.GenerativeModel(model_name_from_env)
                    prompt = (
                        f"以下のファイル内容を分析し、このファイルがプロジェクト全体の中でどのような機能的役割を果たしているかを簡潔に説明してください。\n\n"
                        f"ファイルパス: {file_path}\n\n"
                        f"ファイル内容:\n"
                        f"```\n{file_content[:10000]}\n```\n\n" # Limit content length for API
                        f"このファイルの主な目的と、プロジェクトの他の部分とどのように連携する可能性があるかについて、1～3文でまとめてください。"
                    )
                    ai_response = model.generate_content(prompt)
                    analysis_result = ai_response.text.replace('\n', '<br>')
                except Exception as e:
                    error = f"AI分析中にエラーが発生しました: {e}"
                    analysis_result = "AI分析の実行中にエラーが発生しました。".replace('\n', '<br>') # Also apply here for consistency
            elif not GEMINI_API_KEY:
                analysis_result = "GEMINI_API_KEYが設定されていないため、AI分析は実行できませんでした。ファイルの内容は取得されました。".replace('\n', '<br>')
                # If no API key, we can still show the file content for manual review if desired
                # Or simply state analysis cannot be performed.

            # Ensure all paths leading to analysis_result apply the nl2br equivalent
            if analysis_result == "分析はまだ実行されていません。": # Default initial value
                 analysis_result = analysis_result.replace('\n', '<br>')


    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            error = f"ファイル '{file_path}' がコミット '{commit_sha}' に見つかりません。"
        else:
            error = f"GitHub APIエラー ({e.response.status_code}): {e}"
    except requests.exceptions.RequestException as e:
        error = f"ネットワークエラー: {e}"
    except Exception as e:
        error = f"予期せぬエラーが発生しました: {e}"

    return render_template('analysis_result.html',
                           repo_url=repo_url,
                           commit_sha=commit_sha,
                           file_path=file_path,
                           analysis_result=analysis_result,
                           file_content=file_content, # Pass content for display
                           error=error,
                           branch_name=branch_name,
                           pat=pat)


@app.route('/batch_analyze_files', methods=['POST'])
def batch_analyze_files():
    repo_url = request.form.get('repo_url')
    commit_sha = request.form.get('commit_sha')
    branch_name = request.form.get('branch_name') # For context, might not be strictly needed for file fetching if commit_sha is absolute
    pat = request.form.get('pat')
    selected_files_paths = request.form.getlist('selected_files')

    if not all([repo_url, commit_sha, selected_files_paths]):
        error = "リポジトリURL、コミットSHA、および少なくとも1つのファイルを選択する必要があります。"
        # How to render back to commit_files.html with an error?
        # We might need to fetch the full file list again for that commit to re-render the page.
        # For now, let's return a simple error page or redirect.
        return render_template('analysis_result.html', error=error, repo_url=repo_url, commit_sha=commit_sha, branch_name=branch_name, pat=pat)

    analyzed_data_for_template = []
    all_initial_analyses_texts = [] # To collect texts for second pass AI
    db_session = SessionLocal()

    try:
        parts = repo_url.strip('/').split('/')
        user, repo = parts[-2], parts[-1]

        FILES_PER_BATCH = 1  # Number of files to include in each consolidated AI call
                             # TODO: Increase to >1 once robust AI response parsing for batches is implemented.
        files_to_process_in_current_batch = [] # List of dicts: {'file_path': fp, 'content': fc}

        # Iterate through all selected files to prepare batches
        for file_idx, file_path in enumerate(selected_files_paths):
            file_content_text = None
            analysis_text = "分析できませんでした。" # Default if analysis fails for a file
            cached_this_file = False

            # Step 4: Check if already analyzed (Cache Check)
            existing_analysis = db_session.query(AnalyzedFile).filter_by(
                repo_url=repo_url,
                commit_sha=commit_sha,
                file_path=file_path
            ).first()

            if existing_analysis and existing_analysis.initial_analysis_text:
                file_content_text = existing_analysis.file_content
                analysis_text = existing_analysis.initial_analysis_text
                # Ensure that even cached results are correctly formatted with <br> if needed by template
                # However, the current design applies .replace('\n', '<br>') only on AI model output.
                # For consistency, we might need to store raw text from AI and apply <br> only at display time,
                # or ensure stored text already has <br>. For now, assume stored text is ready for display or raw.
                # Let's assume initial_analysis_text is stored raw and needs formatting if displayed directly.
                # The batch_analysis_result.html will need to handle this if it shows individual analyses.

                analyzed_data_for_template.append({
                    'file_path': file_path,
                    'analysis': analysis_text, # This is the raw text from DB
                    'content': file_content_text,
                    'status': '取得済み (キャッシュ)' # "Retrieved (Cached)"
                })
                all_initial_analyses_texts.append(analysis_text) # Add raw text to list for synthesis
                print(f"Cache hit for {file_path} in commit {commit_sha}")
                cached_this_file = True
                # No continue here, let it fall through to batch processing logic below if it's the last file of a batch
                # but the actual processing of this cached file will be skipped.

            if not cached_this_file:
                try:
                    # Fetch file content
                    api_url = f"https://api.github.com/repos/{user}/{repo}/contents/{file_path}?ref={commit_sha}"
                    headers = {'Accept': 'application/vnd.github.v3+json'}
                    if pat:
                        headers['Authorization'] = f'token {pat}'

                    response = requests.get(api_url, headers=headers)
                    response.raise_for_status()
                    file_data = response.json()

                    if file_data.get('type') == 'file' and 'content' in file_data:
                        file_content_encoded = file_data['content']
                        file_content_bytes = base64.b64decode(file_content_encoded)
                        try:
                            file_content_text = file_content_bytes.decode('utf-8')
                        except UnicodeDecodeError:
                            file_content_text = file_content_bytes.decode('latin-1', errors='replace')

                        files_to_process_in_current_batch.append({
                            'file_path': file_path,
                            'content': file_content_text,
                            'original_idx_in_template_data': len(analyzed_data_for_template) # To map results back
                        })
                        # Add a placeholder to analyzed_data_for_template for this file, to be updated after batch AI call
                        analyzed_data_for_template.append({'file_path': file_path, 'analysis': "処理中...", 'content': file_content_text, 'status': 'Processing'})

                    else: # Not a file or no content
                        analysis_text = f"'{file_path}' はファイルではないか、コンテンツを取得できませんでした。"
                        analyzed_data_for_template.append({'file_path': file_path, 'analysis': analysis_text, 'content': None, 'status': 'Error', 'error_detail': analysis_text})
                        # Does not go into all_initial_analyses_texts if error

                except requests.exceptions.HTTPError as e_file:
                    error_detail = f"ファイル '{file_path}' の取得エラー: {e_file.response.status_code} - {e_file}"
                    analyzed_data_for_template.append({'file_path': file_path, 'analysis': "取得エラー", 'content': None, 'status': 'Error', 'error_detail': error_detail})
                except Exception as e_general_file:
                    error_detail = f"ファイル '{file_path}' のコンテンツ取得中に予期せぬエラー: {e_general_file}"
                    analyzed_data_for_template.append({'file_path': file_path, 'analysis': "取得中エラー", 'content': None, 'status': 'Error', 'error_detail': error_detail})

            # Process the batch if it's full or if it's the last file overall
            if files_to_process_in_current_batch and \
               (len(files_to_process_in_current_batch) == FILES_PER_BATCH or file_idx == len(selected_files_paths) - 1):

                if not GEMINI_API_KEY:
                    for file_in_batch in files_to_process_in_current_batch:
                        # Find the placeholder in analyzed_data_for_template and update it
                        placeholder_idx = file_in_batch['original_idx_in_template_data']
                        analyzed_data_for_template[placeholder_idx]['analysis'] = "GEMINI_API_KEYが設定されていないため、AI分析は実行できませんでした。"
                        analyzed_data_for_template[placeholder_idx]['status'] = "Skipped"
                        # No DB storage for skipped files due to no API KEY
                    files_to_process_in_current_batch = [] # Clear batch
                    continue # Next file in selected_files_paths

                # Construct consolidated prompt for the current batch
                batch_prompt = "以下の複数のファイルについて、それぞれの機能的な役割を簡潔に説明してください。各ファイルの説明は明確に区切ってください。\n\n"
                for i, file_data_in_batch in enumerate(files_to_process_in_current_batch):
                    # Truncate individual file content for the prompt
                    content_snippet = file_data_in_batch['content'][:10000] if file_data_in_batch['content'] else ""
                    batch_prompt += f"--- ファイル {i+1} ---\n"
                    batch_prompt += f"ファイルパス: {file_data_in_batch['file_path']}\n"
                    batch_prompt += f"ファイル内容:\n```\n{content_snippet}\n```\n\n"
                batch_prompt += "各ファイルの役割分析:\n" # Ask AI to provide analyses here

                try:
                    print(f"Processing batch of {len(files_to_process_in_current_batch)} files with AI.")
                    model_name_from_env = os.getenv("GEMINI_MODEL_NAME", "gemini-pro")
                    model = genai.GenerativeModel(model_name_from_env)

                    # Consider overall prompt length limits here too if FILES_PER_BATCH * 10000 is too large
                    ai_batch_response_text = model.generate_content(batch_prompt).text

                    # --- Parse the AI's consolidated response ---
                    # This parsing needs to be robust. For now, a simple split based on a delimiter.
                    # Expecting AI to output something like:
                    # "ファイルパス: path/to/file1.py\n役割分析: [summary1]\n\nファイルパス: path/to/file2.py\n役割分析: [summary2]"
                    # Or, more simply, just a sequence of summaries that we map back by order.
                    # Let's try a simpler parsing: Assume AI gives summaries in order, separated by a clear delimiter like "--- 次のファイル ---" or just "\n\n---\n\n"
                    # For now, let's assume a very simple parsing: AI returns summaries separated by "--- FILE BREAK ---"
                    # The prompt should instruct the AI to use such a delimiter.
                    # Modifying prompt slightly:
                    # batch_prompt += "各ファイルの役割分析 (各分析結果を '--- FILE BREAK ---' で区切ってください):\n" (This should be added above)
                    # For now, this part is highly dependent on reliable AI output formatting.
                    # A more robust method would involve asking the AI to return JSON.

                    # Placeholder for actual parsing logic for AI_BATCH_RESPONSE_TEXT
                    # This is a critical and potentially complex part.
                    # For now, let's assume a simple scenario: AI returns summaries in order, one per "paragraph" or separated by "\n\n"
                    # and we map them back to files_to_process_in_current_batch by order.

                    # This is a placeholder for parsing. A real implementation needs robust parsing.
                    # For simplicity in this step, let's assume one summary per file, and we'll distribute them.
                    # This is a MAJOR simplification and likely point of failure without careful prompt engineering and parsing.
                    parsed_summaries = [f"仮の解析結果 {i+1} for {f['file_path']}" for i, f in enumerate(files_to_process_in_current_batch)]
                    if ai_batch_response_text:
                        # Attempt to split by a hypothetical delimiter or make assumptions.
                        # This needs to align with how the AI is prompted to format its output.
                        # For now, if only one file in batch, it's easy. If multiple, this is hard.
                        # Let's assume for now, if len(files_to_process_in_current_batch) == 1, ai_batch_response_text is the summary.
                        # This is a stop-gap. True batch parsing is complex.
                        if len(files_to_process_in_current_batch) == 1:
                             parsed_summaries = [ai_batch_response_text]
                        else:
                            # TODO: Implement robust parsing for multiple files in a single AI response.
                            # For now, assign a generic message if parsing is not implemented for >1 file.
                            parsed_summaries = [f"バッチ応答の解析ロジックが必要です for {f['file_path']}" for f in files_to_process_in_current_batch]


                    for i, file_in_batch in enumerate(files_to_process_in_current_batch):
                        placeholder_idx = file_in_batch['original_idx_in_template_data']
                        current_file_path = file_in_batch['file_path']
                        current_file_content = file_in_batch['content']

                        # This summary mapping is naive if multiple files are in one response without good delimiters.
                        individual_summary = parsed_summaries[i] if i < len(parsed_summaries) else "解析結果のマッピングに失敗しました。"

                        analyzed_data_for_template[placeholder_idx]['analysis'] = individual_summary
                        analyzed_data_for_template[placeholder_idx]['status'] = "Analyzed"
                        all_initial_analyses_texts.append(individual_summary)

                        # Store in DB
                        new_analysis = AnalyzedFile(
                            repo_url=repo_url, commit_sha=commit_sha, file_path=current_file_path,
                            file_content=current_file_content, initial_analysis_text=individual_summary
                        )
                        db_session.add(new_analysis)
                    db_session.commit() # Commit after processing a batch

                except Exception as e_batch_ai:
                    print(f"Error during batch AI call: {e_batch_ai}")
                    for file_in_batch in files_to_process_in_current_batch:
                        placeholder_idx = file_in_batch['original_idx_in_template_data']
                        analyzed_data_for_template[placeholder_idx]['analysis'] = "バッチAI分析中にエラーが発生しました。"
                        analyzed_data_for_template[placeholder_idx]['status'] = "Error"

                files_to_process_in_current_batch = [] # Clear the batch

        # --- Second-Pass AI Feature Synthesis (Step 5) ---
        feature_synthesis_result_text_for_template = "特徴の統合分析は後ほど実装されます。" # Renamed for clarity
        if all_initial_analyses_texts and GEMINI_API_KEY:
            # This is where Step 5 would go.
            # Construct prompt with all_initial_analyses_texts
            # Call Gemini API
            # Store result in FeatureSynthesis table
            if all_initial_analyses_texts and GEMINI_API_KEY:
                try:
                    synthesis_prompt = (
                        "以下の個別のファイル役割分析に基づいて、これらのファイル群がターゲットプロジェクトのどのような主要な機能や全体的な役割に貢献しているかを統合的に説明してください。\n\n"
                        "個別のファイル分析:\n"
                        "--------------------\n"
                    )
                    for i, text in enumerate(all_initial_analyses_texts):
                        # We might need to be careful about prompt length.
                        # For now, just concatenating.
                        synthesis_prompt += f"ファイルセット{i+1}の分析:\n{text}\n\n"

                    synthesis_prompt += "--------------------\n"
                    synthesis_prompt += "統合的な機能説明:\n"

                    model_name_from_env = os.getenv("GEMINI_MODEL_NAME", "gemini-pro") # Use configured model
                    synthesis_model = genai.GenerativeModel(model_name_from_env)

                    # Limit prompt length if necessary, though Gemini Pro has a large context window
                    # For extremely large number of files, this might need chunking or summarization of summaries.
                    MAX_SYNTHESIS_PROMPT_CHARS = 30000 # Example limit
                    ai_synthesis_response = synthesis_model.generate_content(synthesis_prompt[:MAX_SYNTHESIS_PROMPT_CHARS])
                    raw_synthesis_text = ai_synthesis_response.text

                    # Store the raw synthesis result
                    new_synthesis = FeatureSynthesis(
                        commit_sha=commit_sha, # Or a more specific batch_id if implemented
                        synthesis_text=raw_synthesis_text
                    )
                    db_session.add(new_synthesis)
                    db_session.commit()
                    print(f"Feature synthesis stored for commit {commit_sha}")
                    feature_synthesis_result_text_for_template = raw_synthesis_text.replace('\n', '<br>')

                except Exception as e_synth:
                    print(f"Error during feature synthesis: {e_synth}")
                    feature_synthesis_result_text_for_template = f"特徴の統合分析中にエラーが発生しました: {e_synth}".replace('\n', '<br>')
            elif not GEMINI_API_KEY:
                feature_synthesis_result_text_for_template = "GEMINI_API_KEYが設定されていないため、特徴の統合分析は実行できませんでした。" # Already single line
            else: # No initial analyses to synthesize
                feature_synthesis_result_text_for_template = "分析対象のファイルがないため、特徴の統合分析は行われませんでした。" # Already single line

            # Prepare individual analysis texts for template (with <br>)
            # The 'analysis' field in analyzed_data_for_template currently holds raw text from DB or AI
            # We need to format it for the template.
            formatted_analyzed_files_data = []
            for ad in analyzed_data_for_template:
                # Ensure 'analysis' key exists and is a string before replacing
                analysis_content = ad.get('analysis', '')
                if not isinstance(analysis_content, str):
                    analysis_content = str(analysis_content) # Convert to string if not already

                formatted_ad = ad.copy() # Avoid modifying original dict in list
                formatted_ad['analysis'] = analysis_content.replace('\n', '<br>')
                formatted_analyzed_files_data.append(formatted_ad)


        return render_template('batch_analysis_result.html', # New template needed
                               repo_url=repo_url,
                               commit_sha=commit_sha,
                               branch_name=branch_name,
                               analyzed_files_data=formatted_analyzed_files_data, # Pass formatted data
                               feature_synthesis=feature_synthesis_result_text_for_template, # Pass formatted data
                               pat=pat)

    except Exception as e_batch:
        # Broader error handling for the batch process itself
        db_session.rollback() # Rollback if batch fails mid-way and not committing per file
        error = f"バッチ分析処理中にエラーが発生しました: {e_batch}"
        return render_template('analysis_result.html', error=error, repo_url=repo_url, commit_sha=commit_sha, branch_name=branch_name, pat=pat)
    finally:
        db_session.close()


if __name__ == '__main__':
    init_db() # Initialize the database and create tables if they don't exist
    app.run(debug=True)
