//! Git 集成
//!
//! 对应原 Electron `dugite` 调用，改为 `std::process::Command` 直接调 `git` CLI。
//! 输出格式与原 dugite 实现完全一致，前端 GitPanel 无需感知底层切换。
//!
//! 8 个命令：status / log / branches / checkout / stage / unstage / commit / discardChanges / getDiff

use std::process::Command;

use serde::{Deserialize, Serialize};

// =============================================================================
// 类型定义（对应 frontend/shared/api-types.ts 的 Git* 类型）
// =============================================================================

/// 文件状态（对应 `GitFileStatus`）。
#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum GitFileStatus {
    Added,
    Modified,
    Deleted,
    Renamed,
    Untracked,
    Conflict,
}

/// 单个文件的状态条目（对应 `GitStatusEntry`）。
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct GitStatusEntry {
    pub path: String,
    pub status: GitFileStatus,
    pub staged: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub original_path: Option<String>,
}

/// 仓库整体状态（对应 `GitRepoStatus`）。
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct GitRepoStatus {
    pub current_branch: String,
    pub ahead: u32,
    pub behind: u32,
    pub clean: bool,
    pub is_git_repo: bool,
}

/// `git:getStatus` 返回结构。
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct GitStatusResult {
    pub entries: Vec<GitStatusEntry>,
    pub repo_status: GitRepoStatus,
}

/// 单条提交记录（对应 `GitCommit`）。
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct GitCommit {
    pub hash: String,
    pub short_hash: String,
    pub message: String,
    pub author: String,
    pub email: String,
    pub date: String,
    pub parents: Vec<String>,
}

/// `git:getLog` 返回结构。
#[derive(Debug, Serialize)]
pub struct GitLogResult {
    pub commits: Vec<GitCommit>,
}

/// 单条分支信息（对应 `GitBranch`）。
#[derive(Debug, Clone, Serialize)]
pub struct GitBranch {
    pub name: String,
    pub current: bool,
    pub remote: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub upstream: Option<String>,
}

/// `git:getBranches` 返回结构。
#[derive(Debug, Serialize)]
pub struct GitBranchesResult {
    pub branches: Vec<GitBranch>,
}

/// Git 操作结果（对应 `{ ok: boolean; error?: string }`）。
#[derive(Debug, Serialize)]
pub struct GitOpResult {
    pub ok: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

/// `git:getDiff` 返回结构。
#[derive(Debug, Serialize)]
pub struct GitDiffResult {
    pub diff: String,
}

// =============================================================================
// CLI 辅助函数
// =============================================================================

/// 执行 `git` 命令，返回 (stdout, stderr, exit_code)。
pub fn exec_git(args: &[&str], repo_path: &str) -> (String, String, i32) {
    let output = Command::new("git")
        .args(args)
        .current_dir(repo_path)
        .output();

    match output {
        Ok(o) => (
            String::from_utf8_lossy(&o.stdout).to_string(),
            String::from_utf8_lossy(&o.stderr).to_string(),
            o.status.code().unwrap_or(-1),
        ),
        Err(e) => (String::new(), e.to_string(), -1),
    }
}

/// 检查路径是否为 git 仓库（存在 `.git` 目录）。
pub fn is_git_repo(repo_path: &str) -> bool {
    let git_dir = std::path::Path::new(repo_path).join(".git");
    git_dir.exists()
}

// =============================================================================
// 命令实现
// =============================================================================

/// `git:getStatus` → `git status --porcelain -u --branch`
///
/// 解析逻辑与 [frontend/main/index.ts:566-677](file:///d:/java/agentprojects/agentx/frontend/main/index.ts#L566-L677) 一致。
pub fn get_status(repo_path: &str) -> GitStatusResult {
    if !is_git_repo(repo_path) {
        return GitStatusResult {
            entries: vec![],
            repo_status: GitRepoStatus {
                current_branch: String::new(),
                ahead: 0,
                behind: 0,
                clean: true,
                is_git_repo: false,
            },
        };
    }

    let (stdout, _, exit_code) = exec_git(&["status", "--porcelain", "-u", "--branch"], repo_path);
    if exit_code != 0 {
        return GitStatusResult {
            entries: vec![],
            repo_status: GitRepoStatus {
                current_branch: String::new(),
                ahead: 0,
                behind: 0,
                clean: true,
                is_git_repo: true,
            },
        };
    }

    let mut entries: Vec<GitStatusEntry> = Vec::new();
    let mut current_branch = String::new();
    let mut ahead = 0u32;
    let mut behind = 0u32;

    for line in stdout.lines().filter(|l| !l.trim().is_empty()) {
        // 分支信息行：## branch...remote [ahead N, behind N]
        if let Some(branch_info) = line.strip_prefix("## ") {
            // 解析格式：branch...remote [ahead N, behind N]
            if let Some(bracket_start) = branch_info.find('[') {
                let bracket_end = branch_info.find(']').unwrap_or(branch_info.len());
                let tracking = &branch_info[bracket_start + 1..bracket_end];
                // ahead N
                if let Some(pos) = tracking.find("ahead ") {
                    let rest = &tracking[pos + 6..];
                    let num: String = rest.chars().take_while(|c| c.is_ascii_digit()).collect();
                    if let Ok(n) = num.parse::<u32>() {
                        ahead = n;
                    }
                }
                // behind N
                if let Some(pos) = tracking.find("behind ") {
                    let rest = &tracking[pos + 7..];
                    let num: String = rest.chars().take_while(|c| c.is_ascii_digit()).collect();
                    if let Ok(n) = num.parse::<u32>() {
                        behind = n;
                    }
                }
            }
            // 提取分支名（... 之前的部分）
            if let Some(dot_pos) = branch_info.find("...") {
                current_branch = branch_info[..dot_pos].trim().to_string();
            } else {
                current_branch = branch_info.trim().to_string();
            }
            // 去掉可能的 [ahead/behind] 后缀
            if let Some(bracket_pos) = current_branch.find('[') {
                current_branch.truncate(bracket_pos);
                current_branch = current_branch.trim().to_string();
            }
            continue;
        }

        // 文件状态行：XY <path>
        let chars: Vec<char> = line.chars().collect();
        if chars.len() < 3 {
            continue;
        }
        let staged = chars[0];
        let unstaged = chars[1];
        let raw_path = line[3..].to_string();

        let status = if staged == 'A' || unstaged == 'A' {
            GitFileStatus::Added
        } else if staged == 'M' || unstaged == 'M' {
            GitFileStatus::Modified
        } else if staged == 'D' || unstaged == 'D' {
            GitFileStatus::Deleted
        } else if staged == 'R' || unstaged == 'R' {
            GitFileStatus::Renamed
        } else if staged == 'U' || unstaged == 'U' || staged == 'C' || unstaged == 'C' {
            GitFileStatus::Conflict
        } else {
            GitFileStatus::Untracked
        };

        let is_staged = staged != ' ' && staged != '?';
        let is_untracked = staged == '?' && unstaged == '?';

        // 重命名文件路径解析：originalPath -> newPath
        // git porcelain 格式：`R  oldpath -> newpath`
        let (file_path, original_path) =
            if matches!(status, GitFileStatus::Renamed) && raw_path.contains(" -> ") {
                let parts: Vec<&str> = raw_path.splitn(2, " -> ").collect();
                (
                    parts.get(1).unwrap_or(&"").to_string(),
                    Some(parts.first().unwrap_or(&"").to_string()),
                )
            } else {
                (raw_path, None)
            };

        entries.push(GitStatusEntry {
            path: file_path,
            status: if is_untracked {
                GitFileStatus::Untracked
            } else {
                status
            },
            staged: is_staged,
            original_path,
        });
    }

    let clean = entries.is_empty();
    GitStatusResult {
        entries,
        repo_status: GitRepoStatus {
            current_branch,
            ahead,
            behind,
            clean,
            is_git_repo: true,
        },
    }
}

/// `git:getLog` → `git log --pretty=format:%H|%h|%s|%an|%ae|%ad|%P --date=iso -n{limit}`
pub fn get_log(repo_path: &str, limit: usize) -> GitLogResult {
    let limit_str = format!("-n{}", limit);
    let format = "%H|%h|%s|%an|%ae|%ad|%P";
    let (stdout, _, exit_code) = exec_git(
        &[
            "log",
            &format!("--pretty=format:{}", format),
            "--date=iso",
            &limit_str,
        ],
        repo_path,
    );
    if exit_code != 0 {
        return GitLogResult { commits: vec![] };
    }

    let commits: Vec<GitCommit> = stdout
        .lines()
        .filter(|l| !l.is_empty())
        .map(|line| {
            let parts: Vec<&str> = line.splitn(7, '|').collect();
            GitCommit {
                hash: parts.first().unwrap_or(&"").to_string(),
                short_hash: parts.get(1).unwrap_or(&"").to_string(),
                message: parts.get(2).unwrap_or(&"").to_string(),
                author: parts.get(3).unwrap_or(&"").to_string(),
                email: parts.get(4).unwrap_or(&"").to_string(),
                date: parts.get(5).unwrap_or(&"").to_string(),
                parents: parts
                    .get(6)
                    .map(|s| s.split_whitespace().map(String::from).collect())
                    .unwrap_or_default(),
            }
        })
        .collect();

    GitLogResult { commits }
}

/// `git:getBranches` → `git branch -a -vv`
pub fn get_branches(repo_path: &str) -> GitBranchesResult {
    let (stdout, _, exit_code) = exec_git(&["branch", "-a", "-vv"], repo_path);
    if exit_code != 0 {
        return GitBranchesResult { branches: vec![] };
    }

    let mut branches: Vec<GitBranch> = Vec::new();
    for line in stdout.lines().filter(|l| !l.trim().is_empty()) {
        let current = line.starts_with('*');
        // 去掉前缀 * 或空格
        let trimmed = line.trim_start_matches(|c: char| c == '*' || c.is_whitespace());
        // 第一个 token 是分支名
        let raw_name = trimmed.split_whitespace().next().unwrap_or("");
        let remote = raw_name.starts_with("remotes/");
        let clean_name = if remote {
            raw_name.strip_prefix("remotes/").unwrap_or(raw_name)
        } else {
            raw_name
        };
        // 解析 upstream：[origin/main]
        let upstream = line.find('[').and_then(|start| {
            let end = line[start..].find(']').map(|e| start + e)?;
            Some(line[start + 1..end].to_string())
        });

        branches.push(GitBranch {
            name: clean_name.to_string(),
            current,
            remote,
            upstream,
        });
    }

    GitBranchesResult { branches }
}

/// `git:checkout` → `git checkout <branch>`
pub fn checkout(repo_path: &str, branch: &str) -> GitOpResult {
    let (_, stderr, exit_code) = exec_git(&["checkout", branch], repo_path);
    if exit_code != 0 {
        GitOpResult {
            ok: false,
            error: Some(if stderr.is_empty() {
                "checkout failed".into()
            } else {
                stderr
            }),
        }
    } else {
        GitOpResult {
            ok: true,
            error: None,
        }
    }
}

/// `git:stage` → `git add -- <files>`
pub fn stage(repo_path: &str, files: &[String]) -> GitOpResult {
    let args: Vec<&str> = std::iter::once("add")
        .chain(std::iter::once("--"))
        .chain(files.iter().map(|s| s.as_str()))
        .collect();
    let (_, stderr, exit_code) = exec_git(&args, repo_path);
    if exit_code != 0 {
        GitOpResult {
            ok: false,
            error: Some(if stderr.is_empty() {
                "stage failed".into()
            } else {
                stderr
            }),
        }
    } else {
        GitOpResult {
            ok: true,
            error: None,
        }
    }
}

/// `git:unstage` → `git reset HEAD -- <files>`
pub fn unstage(repo_path: &str, files: &[String]) -> GitOpResult {
    let args: Vec<&str> = std::iter::once("reset")
        .chain(std::iter::once("HEAD"))
        .chain(std::iter::once("--"))
        .chain(files.iter().map(|s| s.as_str()))
        .collect();
    let (_, stderr, exit_code) = exec_git(&args, repo_path);
    if exit_code != 0 {
        GitOpResult {
            ok: false,
            error: Some(if stderr.is_empty() {
                "unstage failed".into()
            } else {
                stderr
            }),
        }
    } else {
        GitOpResult {
            ok: true,
            error: None,
        }
    }
}

/// `git:commit` → `git commit -m <message>`
pub fn commit(repo_path: &str, message: &str) -> GitOpResult {
    let (_, stderr, exit_code) = exec_git(&["commit", "-m", message], repo_path);
    if exit_code != 0 {
        GitOpResult {
            ok: false,
            error: Some(if stderr.is_empty() {
                "commit failed".into()
            } else {
                stderr
            }),
        }
    } else {
        GitOpResult {
            ok: true,
            error: None,
        }
    }
}

/// `git:discardChanges` → `git checkout -- <files>`
pub fn discard_changes(repo_path: &str, files: &[String]) -> GitOpResult {
    let args: Vec<&str> = std::iter::once("checkout")
        .chain(std::iter::once("--"))
        .chain(files.iter().map(|s| s.as_str()))
        .collect();
    let (_, stderr, exit_code) = exec_git(&args, repo_path);
    if exit_code != 0 {
        GitOpResult {
            ok: false,
            error: Some(if stderr.is_empty() {
                "discard failed".into()
            } else {
                stderr
            }),
        }
    } else {
        GitOpResult {
            ok: true,
            error: None,
        }
    }
}

/// `git:getDiff` → `git diff [-- <file>]`
pub fn get_diff(repo_path: &str, file: Option<&str>) -> GitDiffResult {
    let args: Vec<&str> = if let Some(f) = file {
        vec!["diff", "--", f]
    } else {
        vec!["diff"]
    };
    let (stdout, _, exit_code) = exec_git(&args, repo_path);
    if exit_code != 0 {
        return GitDiffResult {
            diff: String::new(),
        };
    }
    GitDiffResult { diff: stdout }
}
