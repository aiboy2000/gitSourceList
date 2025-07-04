from flask import Flask, render_template, request
import requests
import re # For regex matching of file patterns
import os # For API Key and other environment variables
from dotenv import load_dotenv # For loading .env file

app = Flask(__name__)

load_dotenv() # Load variables from .env file into environment

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

        response = requests.get(api_url, headers=headers)
        response.raise_for_status()
        commit_data = response.json()

        # Removed suggestion logic. Now simply list files.
        if 'files' in commit_data:
            for file_info in commit_data['files']:
                files.append({
                    'filename': file_info['filename'],
                    'status': file_info['status']
                })
        # Fallback for commits without explicit 'files' list (e.g., initial commit, merge commits sometimes)
        # This part tries to list all files from the commit's tree if the detailed 'files' array isn't present.
        elif 'commit' in commit_data and 'tree' in commit_data['commit'] and not files :
            tree_sha = commit_data['commit']['tree']['sha']
            tree_api_url = f"https://api.github.com/repos/{user}/{repo}/git/trees/{tree_sha}?recursive=1"
            tree_response = requests.get(tree_api_url, headers=headers)
            tree_response.raise_for_status()
            tree_data = tree_response.json()

            if 'tree' in tree_data:
                for item in tree_data['tree']:
                    if item['type'] == 'blob': # Only include files, not directories
                        files.append({
                            'filename': item['path'],
                            'status': '不明', # Status is not available from tree view like this
                        })
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

if __name__ == '__main__':
    app.run(debug=True)
