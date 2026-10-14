// Windows installer engine. No network, process termination or machine-wide changes.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;

namespace HVPCSetup
{
    public sealed class SetupProblem : Exception
    {
        public readonly string Code;
        public SetupProblem(string code) : base(code) { Code = code; }
    }

    public sealed class InstallResult
    {
        public string Folder;
        public string BackupFolder;
    }

    public static class SetupCore
    {
        public const string Product = "HVPC Resolve-MVSEP";
        public const string LauncherName = Product + ".py";
        static readonly UTF8Encoding Utf8 = new UTF8Encoding(false);
        static readonly JavaScriptSerializer Json = new JavaScriptSerializer();

        public static string Full(string path)
        {
            if (String.IsNullOrWhiteSpace(path) || !Regex.IsMatch(path, @"^[A-Za-z]:[\\/]"))
                throw new SetupProblem("folder");
            string result = Path.GetFullPath(path);
            if (result.Length > 180 || result.StartsWith(@"\\") ||
                result.TrimEnd('\\', '/') == Path.GetPathRoot(result).TrimEnd('\\', '/'))
                throw new SetupProblem("folder");
            return result.TrimEnd('\\', '/');
        }

        static bool Same(string a, string b) { return String.Equals(a, b, StringComparison.OrdinalIgnoreCase); }
        static bool Inside(string path, string root) { return path.StartsWith(root + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase); }

        static void NoLinks(string path)
        {
            for (string current = path; !String.IsNullOrEmpty(current); current = Path.GetDirectoryName(current))
            {
                if ((File.Exists(current) || Directory.Exists(current)) &&
                    (File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0)
                    throw new SetupProblem("linked_folder");
            }
        }

        public static bool IsInstallation(string path)
        {
            return File.Exists(Path.Combine(path, "src", "resolve_mvsep", "main.py")) &&
                   File.Exists(Path.Combine(path, "scripts", LauncherName));
        }

        public static string FindInstalledFolder(string utility)
        {
            try
            {
                string text = File.ReadAllText(Path.Combine(utility, LauncherName), Utf8);
                Match match = Regex.Match(text, @"(?m)^PROJECT_ROOT\s*=\s*(.*)\s*$");
                string literal = match.Groups[1].Value.Trim();
                string path = null;
                if (literal.StartsWith("r\"") && literal.EndsWith("\""))
                    path = literal.Substring(2, literal.Length - 3);
                else if (literal.StartsWith("\"")) path = Json.Deserialize<string>(literal);
                if (!String.IsNullOrEmpty(path) && IsInstallation(path)) return Full(path);
            }
            catch { /* Detection is optional; never execute the installed launcher. */ }
            return null;
        }

        public static bool HasToken(string target)
        {
            try
            {
                var data = ReadObject(Path.Combine(target, "config", "mvsep.json"));
                return data.ContainsKey("api_token") && data["api_token"] is string &&
                       !String.IsNullOrWhiteSpace((string)data["api_token"]);
            }
            catch { return false; }
        }

        static Dictionary<string, object> ReadObject(string path)
        {
            try
            {
                var result = Json.DeserializeObject(File.ReadAllText(path, Utf8)) as Dictionary<string, object>;
                if (result == null) throw new Exception();
                return result;
            }
            catch { throw new SetupProblem("config"); } // Never expose a parser excerpt containing the token.
        }

        static byte[] Encode(object value) { return Utf8.GetBytes(Json.Serialize(value) + "\n"); }

        static IEnumerable<string> PythonFiles(string root, string relative)
        {
            string directory = Path.Combine(root, relative);
            NoLinks(directory);
            foreach (string file in Directory.GetFiles(directory, "*.py"))
            {
                NoLinks(file);
                yield return Path.Combine(relative, Path.GetFileName(file));
            }
            foreach (string sub in Directory.GetDirectories(directory))
            {
                if (Path.GetFileName(sub) == "__pycache__") continue;
                foreach (string file in PythonFiles(root, Path.Combine(relative, Path.GetFileName(sub)))) yield return file;
            }
        }

        // Deliberately bounded payload: no config, jobs, outputs, logs or developer files.
        public static Dictionary<string, byte[]> Payload(string source)
        {
            source = Full(source);
            NoLinks(source);
            if (!IsInstallation(source)) throw new SetupProblem("package");
            var names = new List<string> {
                "README.md", "README.en.md", "LICENSE", "settings.example.json", "mvsep.example.json",
                "src/resolve_mvsep/progress.manifest",
                "src/resolve_mvsep/app.ico",
                "Install.cmd", "installer/SetupCore.cs", "installer/SetupForm.cs", "installer/app.manifest",
                "scripts/" + LauncherName
            };
            names.AddRange(PythonFiles(source, "src"));
            // The packaged release includes this; source ZIPs compile through Install.cmd.
            if (File.Exists(Path.Combine(source, "Install.exe"))) names.Add("Install.exe");
            var files = new Dictionary<string, byte[]>(StringComparer.OrdinalIgnoreCase);
            foreach (string name in names)
            {
                string path = Path.Combine(source, name.Replace('/', Path.DirectorySeparatorChar));
                NoLinks(path);
                if (!File.Exists(path)) throw new SetupProblem("package");
                files[name.Replace('/', Path.DirectorySeparatorChar)] = File.ReadAllBytes(path);
            }
            return files;
        }

        public static string Launcher(byte[] template, string target)
        {
            string text = Utf8.GetString(template);
            if (!Regex.IsMatch(text, @"(?m)^PROJECT_ROOT = """"\r?$")) throw new SetupProblem("package");
            text = Regex.Replace(text, @"(?m)^PROJECT_ROOT = """"\r?$", delegate(Match _) {
                return "PROJECT_ROOT = " + Json.Serialize(target);
            });
            // A wizard-installed menu entry must open its selected folder even if an old
            // optional environment variable still exists. No environment edits are needed.
            return text.Replace("os.environ.get(\"RESOLVE_MVSEP_HOME\") or PROJECT_ROOT",
                                "PROJECT_ROOT or os.environ.get(\"RESOLVE_MVSEP_HOME\")");
        }

        static void AtomicWrite(string path, byte[] content, HashSet<string> createdDirectories = null)
        {
            string directory = Path.GetDirectoryName(path);
            if (createdDirectories != null)
                for (string parent = directory; !String.IsNullOrEmpty(parent) && !Directory.Exists(parent); parent = Path.GetDirectoryName(parent))
                    createdDirectories.Add(parent);
            Directory.CreateDirectory(directory);
            string temporary = Path.Combine(directory, ".hvpc-" + Guid.NewGuid().ToString("N") + ".tmp");
            try
            {
                File.WriteAllBytes(temporary, content);
                if (File.Exists(path)) File.Replace(temporary, path, null);
                else File.Move(temporary, path);
            }
            finally { if (File.Exists(temporary)) File.Delete(temporary); }
        }

        public static InstallResult Install(string source, string target, string utility, string token,
                                            string language, Action<int> beforeWrite = null)
        {
            source = Full(source); target = Full(target); utility = Full(utility);
            NoLinks(target); NoLinks(utility);
            if ((!Same(source, target) && (Inside(target, source) || (Inside(source, target) && !IsInstallation(target)))) ||
                Same(target, utility) || Inside(utility, target) || Inside(target, utility))
                throw new SetupProblem("nested_folder");
            if (Directory.Exists(target) && Directory.EnumerateFileSystemEntries(target).Any() && !IsInstallation(target))
                throw new SetupProblem("occupied_folder");
            token = (token ?? "").Trim();
            if (token.Any(Char.IsWhiteSpace) || token.Any(Char.IsControl) || token.Length > 4096)
                throw new SetupProblem("token");
            var payload = Payload(source);
            string configPath = Path.Combine(target, "config", "mvsep.json");
            string settingsPath = Path.Combine(target, "settings.json");
            string uiPath = Path.Combine(target, "config", "ui.json");
            NoLinks(configPath); NoLinks(settingsPath); NoLinks(uiPath);
            var config = ReadObject(File.Exists(configPath) ? configPath : Path.Combine(source, "mvsep.example.json"));
            if (String.IsNullOrEmpty(token) && (!config.ContainsKey("api_token") ||
                !(config["api_token"] is string) || String.IsNullOrWhiteSpace((string)config["api_token"])))
                throw new SetupProblem("token_missing");
            if (!String.IsNullOrEmpty(token)) config["api_token"] = token;
            if (File.Exists(settingsPath)) ReadObject(settingsPath);
            var ui = File.Exists(uiPath) ? ReadObject(uiPath) : new Dictionary<string, object>();
            ui["language"] = language == "en" ? "en" : "ru";
            ui["version"] = 1;

            var writes = new List<KeyValuePair<string, byte[]>>();
            foreach (var file in payload) writes.Add(new KeyValuePair<string, byte[]>(Path.Combine(target, file.Key), file.Value));
            if (!File.Exists(settingsPath)) writes.Add(new KeyValuePair<string, byte[]>(settingsPath, payload["settings.example.json"]));
            if (!File.Exists(configPath) || !String.IsNullOrEmpty(token)) writes.Add(new KeyValuePair<string, byte[]>(configPath, Encode(config)));
            writes.Add(new KeyValuePair<string, byte[]>(uiPath, Encode(ui)));
            string launcherPath = Path.Combine(utility, LauncherName);
            writes.Add(new KeyValuePair<string, byte[]>(launcherPath, Utf8.GetBytes(Launcher(payload[Path.Combine("scripts", LauncherName)], target))));

            // Snapshot before any mutation and keep existing bytes for rollback. The old
            // launcher is saved alongside application backups, not in Resolve's menu.
            var previous = new Dictionary<string, byte[]>(StringComparer.OrdinalIgnoreCase);
            foreach (var write in writes)
            {
                NoLinks(write.Key);
                previous[write.Key] = File.Exists(write.Key) ? File.ReadAllBytes(write.Key) : null;
            }
            var changed = writes.Where(w => previous[w.Key] == null || !previous[w.Key].SequenceEqual(w.Value)).ToList();
            string backup = Path.Combine(target, "config", "installer-backups", Guid.NewGuid().ToString("N").Substring(0, 12));
            NoLinks(backup);
            var createdDirectories = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            var backupFiles = new List<string>();
            var applied = new List<string>();
            try
            {
                var restoreMap = new Dictionary<string, string>();
                foreach (var write in changed)
                {
                    if (previous[write.Key] == null) continue;
                    // Flat names avoid exceeding Windows' legacy path limit when the
                    // working folder is long. The map records each original location.
                    string name = restoreMap.Count.ToString("D4") + ".bak";
                    string backupFile = Path.Combine(backup, name);
                    AtomicWrite(backupFile, previous[write.Key], createdDirectories);
                    backupFiles.Add(backupFile);
                    restoreMap[name] = write.Key;
                }
                if (restoreMap.Count > 0)
                {
                    string mapFile = Path.Combine(backup, "restore-map.json");
                    AtomicWrite(mapFile, Encode(restoreMap), createdDirectories);
                    backupFiles.Add(mapFile);
                }
                int index = 0;
                foreach (var write in changed)
                {
                    if (beforeWrite != null) beforeWrite(index++);
                    AtomicWrite(write.Key, write.Value, createdDirectories);
                    applied.Add(write.Key);
                }
            }
            catch
            {
                bool restored = true;
                applied.Reverse();
                foreach (string path in applied)
                {
                    try
                    {
                        if (previous[path] == null) File.Delete(path);
                        else AtomicWrite(path, previous[path]);
                    }
                    catch { restored = false; }
                }
                if (restored)
                {
                    // Remove only this transaction's temporary backup files and newly
                    // created empty directories, so a failed fresh install can be retried.
                    foreach (string path in backupFiles) { try { File.Delete(path); } catch { } }
                    foreach (string directory in createdDirectories.OrderByDescending(p => p.Length))
                    {
                        try { if (Directory.Exists(directory) && !Directory.EnumerateFileSystemEntries(directory).Any()) Directory.Delete(directory); }
                        catch { }
                    }
                }
                throw new SetupProblem(restored ? "write_failed" : "rollback_failed");
            }
            return new InstallResult { Folder = target, BackupFolder = Directory.Exists(backup) ? backup : null };
        }
    }
}
