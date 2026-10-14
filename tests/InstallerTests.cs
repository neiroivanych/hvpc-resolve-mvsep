// Isolated installer integration tests; never use AppData or the live Resolve menu.
using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using HVPCSetup;

class InstallerTests
{
    static readonly JavaScriptSerializer Json = new JavaScriptSerializer();
    static string root, scratch, target, utility;
    static void Check(bool value, string message) { if (!value) throw new Exception(message); }
    static void Put(string path, string text) { Directory.CreateDirectory(Path.GetDirectoryName(path)); File.WriteAllText(path, text, new UTF8Encoding(false)); }
    static Dictionary<string, object> Read(string path) { return Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(path)); }
    static string Config { get { return Path.Combine(target, "config", "mvsep.json"); } }
    static InstallResult Install(string token) { return SetupCore.Install(root, target, utility, token, "ru"); }
    static void Reject(Action action, string code)
    {
        try { action(); }
        catch (SetupProblem error) { Check(error.Code == code, "Unexpected diagnostic: " + error.Code); return; }
        throw new Exception("Expected rejection: " + code);
    }

    static void Fresh()
    {
        Install("test-only-token");
        Check((string)Read(Config)["api_token"] == "test-only-token", "Token missing");
        Check((string)Read(Path.Combine(target, "settings.json"))["action"] == "separate", "Default action");
        Check((string)Read(Path.Combine(target, "config", "ui.json"))["language"] == "ru", "Language");
        Check(File.ReadAllBytes(Path.Combine(target, "src", "resolve_mvsep", "progress.manifest")).SequenceEqual(
            File.ReadAllBytes(Path.Combine(root, "src", "resolve_mvsep", "progress.manifest"))), "Windows visual styles manifest missing");
        Check(!Directory.Exists(Path.Combine(target, "jobs")), "Copied jobs");
        Check(!Directory.Exists(Path.Combine(target, "output")), "Copied output");
        Check(!File.Exists(Path.Combine(target, "NOTES.md")), "Copied private notes");
        Check(SetupCore.FindInstalledFolder(utility) == target, "Launcher path");
        string launcher = File.ReadAllText(Path.Combine(utility, SetupCore.LauncherName));
        Check(launcher.Contains("configured = PROJECT_ROOT or os.environ"), "Old environment overrides wizard");
    }

    static void Update()
    {
        Fresh();
        Put(Config, "{\"api_token\":\"keep-me\",\"sep_type\":123,\"output_format\":0}");
        Put(Path.Combine(target, "settings.json"), "{\"action\":\"inspect\",\"track_index\":7}");
        Put(Path.Combine(target, "config", "ui.json"), "{\"language\":\"en\",\"custom\":42}");
        Put(Path.Combine(target, "config", "recent_settings.json"), "private recent settings");
        Put(Path.Combine(target, "jobs", "test", "job.json"), "private job");
        Put(Path.Combine(target, "output", "stem.wav"), "private audio");
        string original = File.ReadAllText(Config);
        var result = Install("");
        Check(File.ReadAllText(Config) == original, "Blank token changed existing config bytes");
        Check((string)Read(Path.Combine(target, "settings.json"))["action"] == "inspect", "Changed settings");
        Check((int)Read(Path.Combine(target, "config", "ui.json"))["custom"] == 42, "Lost UI preferences");
        Check(File.ReadAllText(Path.Combine(target, "output", "stem.wav")) == "private audio", "Lost audio");
        Check(File.ReadAllText(Path.Combine(target, "jobs", "test", "job.json")) == "private job", "Lost jobs");
        Check(File.ReadAllText(Path.Combine(target, "config", "recent_settings.json")) == "private recent settings", "Lost recent settings");
        Check(result.BackupFolder != null, "No backup for changed UI prefs");
        Check(File.Exists(Path.Combine(result.BackupFolder, "restore-map.json")), "Backup restoration map missing");
        Install("replacement-token");
        Check((string)Read(Config)["api_token"] == "replacement-token" && (int)Read(Config)["sep_type"] == 123, "Token replacement lost algorithm");
    }

    static void Rollback()
    {
        Fresh();
        string main = Path.Combine(target, "src", "resolve_mvsep", "main.py");
        Put(main, "old source");
        string launcher = Path.Combine(utility, SetupCore.LauncherName);
        Put(launcher, "previous launcher");
        var before = Directory.GetFiles(target, "*", SearchOption.AllDirectories).ToDictionary(p => p, File.ReadAllBytes);
        bool reached = false;
        Reject(delegate {
            SetupCore.Install(root, target, utility, "new-token", "en", delegate(int index) {
                // Fail the menu write, after application, token and language changes.
                if (File.ReadAllText(Config).Contains("new-token") && (string)Read(Path.Combine(target, "config", "ui.json"))["language"] == "en")
                { reached = true; throw new IOException("injected write failure"); }
            });
        }, "write_failed");
        Check(reached, "Failure injection was not reached");
        foreach (var file in before) Check(File.ReadAllBytes(file.Key).SequenceEqual(file.Value), "Rollback mismatch");
        Check(File.ReadAllText(launcher) == "previous launcher", "Lost previous menu launcher");
        Check(!Directory.Exists(Path.Combine(target, "config", "installer-backups")), "Successful rollback left transaction backups");
    }

    static void FreshRollback()
    {
        Put(Path.Combine(utility, SetupCore.LauncherName), "previous installation launcher");
        Reject(delegate { SetupCore.Install(root, target, utility, "test", "ru", delegate(int i) {
            if (i == 4) throw new IOException("injected disk failure");
        }); }, "write_failed");
        Check(!Directory.Exists(target), "Failed fresh install left a nonempty destination");
        Check(File.ReadAllText(Path.Combine(utility, SetupCore.LauncherName)) == "previous installation launcher", "Lost previous launcher");
        Fresh();
    }

    static void InvalidConfig()
    {
        Fresh();
        Put(Config, "{\"api_token\":\"SECRET-DONT-PRINT");
        Reject(delegate { Install("new-token"); }, "config");
        Check(File.ReadAllText(Config).Contains("SECRET-DONT-PRINT"), "Corrupt config overwritten");
    }

    static void Inputs()
    {
        Reject(delegate { Install(""); }, "token_missing");
        Reject(delegate { Install("not a token"); }, "token");
        Reject(delegate { SetupCore.Install(root, "relative", utility, "test", "ru"); }, "folder");
        Reject(delegate { SetupCore.Install(root, "C:relative", utility, "test", "ru"); }, "folder");
        Reject(delegate { SetupCore.Install(root, Path.GetPathRoot(target), utility, "test", "ru"); }, "folder");
        Reject(delegate { SetupCore.Install(root, Path.Combine(root, "nested-setup-test"), utility, "test", "ru"); }, "nested_folder");
        Check(!Directory.Exists(target), "Invalid input wrote files");
        Put(Path.Combine(target, "keep.txt"), "unrelated files");
        Reject(delegate { Install("test"); }, "occupied_folder");
        Check(Directory.GetFiles(target).Length == 1, "Occupied folder changed");
    }

    static void EscapePath()
    {
        target = Path.Combine(scratch, "Музыка & монтаж 'test' (новое)");
        Fresh();
        Check(SetupCore.FindInstalledFolder(utility) == target, "Path round trip");
    }

    static void SourcePrivacy()
    {
        // Emulate a developer/used application folder, with settings and private media.
        string package = Path.Combine(scratch, "package");
        foreach (var file in SetupCore.Payload(root))
        {
            string path = Path.Combine(package, file.Key);
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            File.WriteAllBytes(path, file.Value);
        }
        Put(Path.Combine(package, "config", "mvsep.json"), "PRIVATE-TOKEN");
        Put(Path.Combine(package, "settings.json"), "PRIVATE-SETTINGS");
        Put(Path.Combine(package, "jobs", "job.json"), "PRIVATE-JOB");
        Put(Path.Combine(package, "output", "song.wav"), "PRIVATE-AUDIO");
        File.Copy(typeof(SetupCore).Assembly.Location, Path.Combine(package, "Install.exe"));
        string original = root; root = package;
        Fresh();
        root = original;
        Check(File.ReadAllBytes(Path.Combine(target, "Install.exe")).SequenceEqual(File.ReadAllBytes(typeof(SetupCore).Assembly.Location)), "Packaged wizard not installed");
        foreach (string file in Directory.GetFiles(target, "*", SearchOption.AllDirectories))
            Check(!File.ReadAllText(file).Contains("PRIVATE-"), "Private source data copied");
    }

    static void BrokenPackage()
    {
        Reject(delegate { SetupCore.Install(Path.Combine(scratch, "missing-package"), target, utility, "test", "en"); }, "package");
        Check(!Directory.Exists(target), "Broken package created target");
    }

    static void ReconfigureInPlace()
    {
        Fresh();
        SetupCore.Install(target, target, utility, "new-token", "en");
        Check((string)Read(Config)["api_token"] == "new-token", "In-place token edit");
        Check((string)Read(Path.Combine(target, "config", "ui.json"))["language"] == "en", "In-place language edit");
    }

    static void NestedUpdate()
    {
        Fresh();
        string package = Path.Combine(target, "build", "downloaded-update");
        foreach (var file in SetupCore.Payload(root))
        {
            string path = Path.Combine(package, file.Key);
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            File.WriteAllBytes(path, file.Value);
        }
        SetupCore.Install(package, target, utility, "", "en");
        Check((string)Read(Config)["api_token"] == "test-only-token", "Nested update changed token");
        Check((string)Read(Path.Combine(target, "config", "ui.json"))["language"] == "en", "Nested update failed");
    }

    static void Render()
    {
        Application.EnableVisualStyles();
        foreach (string lang in new[] { "ru", "en" })
        {
            System.Threading.Thread.CurrentThread.CurrentUICulture = new System.Globalization.CultureInfo(lang);
            using (var form = new SetupForm(root, utility))
            {
                // Create real WinForms handles outside the visible desktop. No live
                // application is controlled and no installation action is invoked.
                form.StartPosition = FormStartPosition.Manual;
                form.Location = new Point(-20000, -20000);
                form.ShowInTaskbar = false;
                form.Show();
                form.PerformLayout();
                using (var bitmap = new Bitmap(form.Width, form.Height))
                {
                    form.DrawToBitmap(bitmap, new Rectangle(Point.Empty, bitmap.Size));
                    bitmap.Save(Path.Combine(scratch, "setup-" + lang + ".png"));
                }
                typeof(SetupForm).GetMethod("ShowComplete", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(form,
                    new object[] { new InstallResult { Folder = target, BackupFolder = Path.Combine(target, "config", "installer-backups", "example") } });
                form.PerformLayout();
                using (var bitmap = new Bitmap(form.Width, form.Height))
                {
                    form.DrawToBitmap(bitmap, new Rectangle(Point.Empty, bitmap.Size));
                    bitmap.Save(Path.Combine(scratch, "complete-" + lang + ".png"));
                }
            }
        }
    }

    [STAThread]
    static int Main(string[] args)
    {
        try
        {
            root = Path.GetFullPath(args[0]); scratch = Path.GetFullPath(args[1]);
            target = Path.Combine(scratch, "working"); utility = Path.Combine(scratch, "fake-Utility");
            switch (args[2])
            {
                case "fresh": Fresh(); break;
                case "update": Update(); break;
                case "rollback": Rollback(); break;
                case "fresh_rollback": FreshRollback(); break;
                case "config": InvalidConfig(); break;
                case "inputs": Inputs(); break;
                case "paths": EscapePath(); break;
                case "privacy": SourcePrivacy(); break;
                case "package": BrokenPackage(); break;
                case "inplace": ReconfigureInPlace(); break;
                case "nested_update": NestedUpdate(); break;
                case "render": Render(); break;
                default: throw new Exception("Unknown test");
            }
            Console.WriteLine("PASS " + args[2]); return 0;
        }
        catch (Exception error) { Console.Error.WriteLine(error); return 1; }
    }
}
