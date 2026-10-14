using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Management;
using System.Threading;
using System.Windows.Forms;

namespace HVPCSetup
{
    public sealed class SetupForm : Form
    {
        readonly string source;
        readonly string utility;
        readonly TableLayoutPanel page = new TableLayoutPanel();
        readonly ComboBox languages = new ComboBox();
        readonly TextBox folder = new TextBox();
        readonly TextBox token = new TextBox();
        readonly CheckBox showToken = new CheckBox();
        readonly Button install = new Button();
        readonly Label title = new Label();
        readonly Label introduction = new Label();
        readonly Label folderLabel = new Label();
        readonly Label folderHint = new Label();
        readonly Label tokenLabel = new Label();
        readonly Label tokenHint = new Label();
        readonly Label status = new Label();
        readonly LinkLabel apiLink = new LinkLabel();
        readonly Button browse = new Button();
        readonly Label closeHint = new Label();
        bool completed;
        bool busy;
        bool English { get { return languages.SelectedIndex == 1; } }
        string T(string ru, string en) { return English ? en : ru; }

        public SetupForm(string package, string menu)
        {
            source = package; utility = menu;
            Text = SetupCore.Product;
            using (var stream = typeof(SetupForm).Assembly.GetManifestResourceStream("HVPCSetup.AppIcon"))
            {
                if (stream != null)
                {
                    using (var image = new Icon(stream)) Icon = (Icon)image.Clone();
                    FormClosed += delegate { Icon.Dispose(); };
                }
            }
            Font = new Font("Segoe UI", 10);
            AutoScaleMode = AutoScaleMode.Dpi;
            BackColor = Color.FromArgb(243, 246, 249);
            StartPosition = FormStartPosition.CenterScreen;
            ClientSize = new Size(760, 690);
            MinimumSize = new Size(660, 640);
            page.Dock = DockStyle.Fill;
            page.Padding = new Padding(26, 20, 26, 20);
            page.ColumnCount = 1;
            page.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
            page.AutoScroll = true;
            Controls.Add(page);
            languages.DropDownStyle = ComboBoxStyle.DropDownList;
            languages.Items.AddRange(new object[] { "RU — Русский", "EN — English" });
            languages.Width = 160;
            languages.SelectedIndex = System.Globalization.CultureInfo.CurrentUICulture.TwoLetterISOLanguageName == "ru" ? 0 : 1;
            languages.Anchor = AnchorStyles.Right;
            Add(languages);
            title.Font = new Font(Font, FontStyle.Bold);
            title.Font = new Font(title.Font.FontFamily, 21, FontStyle.Bold);
            Add(title);
            Add(introduction);
            Add(folderLabel);
            folderLabel.Font = new Font(Font, FontStyle.Bold);
            var folderRow = new TableLayoutPanel { ColumnCount = 2, AutoSize = true, Dock = DockStyle.Top, Margin = new Padding(0) };
            folderRow.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
            folderRow.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
            folder.Dock = DockStyle.Fill;
            browse.AutoSize = true;
            browse.Padding = new Padding(8, 2, 8, 2);
            folderRow.Controls.Add(folder, 0, 0);
            folderRow.Controls.Add(browse, 1, 0);
            Add(folderRow);
            Add(folderHint);
            folderHint.ForeColor = Color.FromArgb(72, 92, 111);
            Add(tokenLabel);
            tokenLabel.Font = new Font(Font, FontStyle.Bold);
            token.UseSystemPasswordChar = true;
            token.MaxLength = 4096;
            token.Dock = DockStyle.Top;
            Add(token);
            Add(showToken);
            showToken.AutoSize = true;
            Add(apiLink);
            Add(tokenHint);
            tokenHint.ForeColor = folderHint.ForeColor;
            Add(closeHint);
            closeHint.Padding = new Padding(12);
            closeHint.BackColor = Color.FromArgb(228, 237, 246);
            Add(status);
            status.ForeColor = Color.FromArgb(145, 48, 66);
            install.AutoSize = true;
            install.MinimumSize = new Size(180, 42);
            install.Anchor = AnchorStyles.Right;
            install.BackColor = Color.FromArgb(49, 91, 127);
            install.ForeColor = Color.White;
            install.FlatStyle = FlatStyle.Flat;
            install.FlatAppearance.BorderSize = 0;
            install.Font = new Font(Font, FontStyle.Bold);
            Add(install);
            AcceptButton = install;
            folder.Text = SetupCore.FindInstalledFolder(utility) ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), SetupCore.Product);
            browse.Click += delegate {
                using (var picker = new FolderBrowserDialog())
                {
                    picker.Description = T("Выберите пустую папку или папку прежней установки HVPC Resolve-MVSEP.", "Choose an empty folder or your existing HVPC Resolve-MVSEP folder.");
                    if (Directory.Exists(folder.Text)) picker.SelectedPath = folder.Text;
                    if (picker.ShowDialog(this) == DialogResult.OK) folder.Text = picker.SelectedPath;
                }
            };
            folder.TextChanged += delegate { RefreshText(); };
            languages.SelectedIndexChanged += delegate { RefreshText(); };
            showToken.CheckedChanged += delegate { token.UseSystemPasswordChar = !showToken.Checked; };
            apiLink.LinkClicked += delegate {
                try { Process.Start(new ProcessStartInfo("https://mvsep.com/" + (English ? "en" : "ru") + "/full_api") { UseShellExecute = true }); }
                catch { status.Text = T("Откройте mvsep.com/ru/full_api в браузере.", "Open mvsep.com/en/full_api in your browser."); }
            };
            install.Click += delegate { if (completed) Close(); else BeginInstall(); };
            FormClosing += delegate(object sender, FormClosingEventArgs e) { if (busy) e.Cancel = true; };
            RefreshText();
        }

        void Add(Control control)
        {
            if (control is Label) { control.AutoSize = true; control.Dock = DockStyle.Top; }
            control.Margin = new Padding(0, 5, 0, 5);
            page.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            page.Controls.Add(control, 0, page.RowCount++);
        }

        void RefreshText()
        {
            if (completed || busy) return;
            Text = SetupCore.Product + T(" — установка", " — setup");
            title.Text = T("Установка", "Setup");
            introduction.Text = T("Мастер подготовит рабочую папку, сохранит токен и добавит пункт запуска в меню DaVinci Resolve.",
                "The wizard prepares your working folder, saves the token and adds a launcher to DaVinci Resolve.");
            folderLabel.Text = T("1. Рабочая папка", "1. Working folder");
            browse.Text = T("Выбрать…", "Browse…");
            bool exists = SetupCore.IsInstallation(folder.Text);
            folderHint.Text = exists ? T("Найдена установка. Настройки, задачи и результаты сохранятся. Изменяемые файлы будут скопированы в резервную папку.",
                "Existing installation found. Settings, tasks and results will be kept. Replaced files will be backed up.") :
                T("Здесь будут программа, настройки и результаты. Можно оставить предложенный путь или выбрать новую пустую папку.",
                "The app, settings and results will live here. Keep this path or choose a new empty folder.");
            tokenLabel.Text = T("2. API-токен MVSEP", "2. MVSEP API token");
            showToken.Text = T("Показать токен", "Show token");
            apiLink.Text = T("Нет токена? Открыть страницу API MVSEP →", "Need a token? Open the MVSEP API page →");
            tokenHint.Text = SetupCore.HasToken(folder.Text) ? T("Токен уже сохранён. Оставьте поле пустым, чтобы сохранить его, или вставьте новый.",
                "A token is already saved. Leave this field empty to keep it, or paste a replacement.") :
                T("Войдите на сайте MVSEP, скопируйте свой API-токен и вставьте сюда. Пароль от аккаунта не нужен. Токен хранится в рабочей папке на этом компьютере.",
                "Sign in on MVSEP, copy your API token and paste it here. Your account password is not needed. The token is stored in the working folder on this computer.");
            closeHint.Text = T("Перед установкой дождитесь завершения разделений и закройте Resolve и окна HVPC Resolve-MVSEP, в том числе значок возле часов.",
                "Before installing, wait for separations to finish and close Resolve and HVPC Resolve-MVSEP, including its tray window.");
            install.Text = exists ? T("Обновить / настроить", "Update / configure") : T("Установить", "Install");
        }

        public static string CheckEnvironment()
        {
            string resolve = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "Blackmagic Design", "DaVinci Resolve");
            if (!File.Exists(Path.Combine(resolve, "ResolvePython", "ResolvePython.exe"))) return "runtime";
            var version = FileVersionInfo.GetVersionInfo(Path.Combine(resolve, "Resolve.exe"));
            if (version.FileMajorPart < 21 || (version.FileMajorPart == 21 && version.FileMinorPart < 1)) return "runtime";
            foreach (string name in new[] { "Resolve", "ResolvePython" })
            {
                var processes = Process.GetProcessesByName(name);
                bool active = processes.Length > 0;
                foreach (var process in processes) process.Dispose();
                if (active) return "running";
            }
            // Also cover workers launched by a standalone Python installation.
            using (var search = new ManagementObjectSearcher("SELECT CommandLine FROM Win32_Process WHERE Name='python.exe' OR Name='pythonw.exe' OR Name='python3.exe'"))
            {
                search.Options.Timeout = TimeSpan.FromSeconds(3);
                using (var processes = search.Get())
                    foreach (ManagementObject process in processes)
                    {
                        using (process)
                        {
                            string command = Convert.ToString(process["CommandLine"]);
                            if (command.IndexOf("mvsep_cli.py", StringComparison.OrdinalIgnoreCase) >= 0) return "running";
                        }
                    }
            }
            return null;
        }

        string Problem(string code)
        {
            switch (code)
            {
                case "runtime": return T("Не найден встроенный Python Resolve 21.1+. Нужен DaVinci Resolve Studio 21.1 или новее в стандартной папке Program Files\\Blackmagic Design\\DaVinci Resolve.", "The Resolve 21.1+ Python runtime was not found. Install DaVinci Resolve Studio 21.1 or later in the default Program Files\\Blackmagic Design\\DaVinci Resolve folder.");
                case "running": return T("Resolve или фоновая задача ещё работает. Дождитесь завершения разделения, закройте Resolve и окна программы, затем повторите установку.", "Resolve or a background task is still running. Wait for the separation to finish, close Resolve and the app windows, then try again.");
                case "package": return T("В архиве не хватает файлов. Распакуйте весь архив выпуска в отдельную папку и запустите мастер оттуда.", "Package files are missing. Extract the entire release ZIP into a separate folder and run setup there.");
                case "folder": return T("Выберите локальную папку с полным путём длиной до 180 символов. Не выбирайте корень диска или сетевую папку.", "Choose a local folder with a full path up to 180 characters. Do not choose a drive root or network folder.");
                case "linked_folder": return T("В пути обнаружена ссылка на другую папку. Выберите обычную локальную папку.", "A linked folder was found in this path. Choose a regular local folder.");
                case "nested_folder": return T("Выберите отдельную рабочую папку вне распакованного архива. Для обновления укажите существующую папку HVPC Resolve-MVSEP.", "Choose a separate working folder outside the extracted archive. To update, select your existing HVPC Resolve-MVSEP folder.");
                case "occupied_folder": return T("Эта папка занята другими файлами. Создайте пустую папку или выберите прежнюю папку HVPC Resolve-MVSEP.", "This folder contains other files. Create an empty folder or choose your existing HVPC Resolve-MVSEP folder.");
                case "token_missing": return T("Вставьте API-токен со страницы MVSEP. Для новой установки он обязателен.", "Paste your API token from MVSEP. A new installation requires it.");
                case "token": return T("Токен содержит пробелы, переносы строк или слишком длинный текст. Скопируйте только значение API-токена.", "The token contains whitespace, line breaks or too much text. Copy only the API token value.");
                case "config": return T("Не удалось прочитать сохранённые настройки. Они не перезаписаны. Проверьте файлы JSON в рабочей папке или выберите новую папку.", "Saved settings could not be read. They have not been overwritten. Check the JSON files in the working folder or choose a new folder.");
                case "write_failed": return T("Не удалось записать файлы. Выполненные изменения отменены. Проверьте доступ к папке и свободное место, затем повторите.", "Files could not be written. Applied changes were rolled back. Check folder permissions and free space, then try again.");
                case "rollback_failed": return T("Установка прервана, восстановить все файлы автоматически не удалось. Резервные копии находятся в config\\installer-backups внутри рабочей папки. Не запускайте программу до восстановления.", "Setup stopped and could not restore every file automatically. Backups are in config\\installer-backups inside the working folder. Restore them before running the app.");
                default: return T("Установка не завершена. Проверьте доступ к папкам и свободное место. Распакуйте весь архив и повторите запуск. Системные настройки и токены в сообщения об ошибках не выводятся.", "Setup did not finish. Check folder permissions and free space, extract the entire ZIP and try again. Error messages do not include settings or tokens.");
            }
        }

        async void BeginInstall()
        {
            string chosenFolder = folder.Text.Trim();
            string chosenToken = token.Text;
            string language = English ? "en" : "ru";
            busy = true;
            page.Enabled = false;
            UseWaitCursor = true;
            status.Text = T("Проверка и установка…", "Checking and installing…");
            try
            {
                InstallResult result = await System.Threading.Tasks.Task.Run(delegate {
                    string issue = CheckEnvironment();
                    if (issue != null) throw new SetupProblem(issue);
                    return SetupCore.Install(source, chosenFolder, utility, chosenToken, language);
                });
                completed = true;
                token.Clear();
                ShowComplete(result);
            }
            catch (SetupProblem error) { status.Text = Problem(error.Code); }
            catch { status.Text = Problem("unknown"); }
            finally { chosenToken = null; busy = false; page.Enabled = true; UseWaitCursor = false; }
        }

        void ShowComplete(InstallResult result)
        {
            page.Controls.Clear();
            page.RowStyles.Clear();
            page.RowCount = 0;
            title.Text = T("Всё готово к запуску", "Ready to start");
            Add(title);
            Add(new Label { Text = T("Рабочая папка: ", "Working folder: ") + result.Folder });
            var instructions = new Label {
                Padding = new Padding(16), BackColor = Color.White,
                Text = T("Один раз в DaVinci Resolve:\n\n1. Откройте Preferences → System → General.\n2. В External scripting using выберите Local. Если изменили значение, сохраните и перезапустите Resolve.\n\nДля разделения:\n\n3. Выделите клип на таймлайне.\n4. Откройте Workspace → Scripts → HVPC Resolve-MVSEP.",
                    "Once in DaVinci Resolve:\n\n1. Open Preferences → System → General.\n2. Set External scripting using to Local. If you changed it, save and restart Resolve.\n\nTo separate audio:\n\n3. Select a clip on the timeline.\n4. Open Workspace → Scripts → HVPC Resolve-MVSEP.")
            };
            Add(instructions);
            Add(new Label { Text = T("Токен сохранён. Подключение к аккаунту MVSEP проверяется при открытии окна разделения. Для смены токена снова запустите этот мастер.",
                "Your token is saved. The MVSEP connection is checked when the separation window opens. Run this wizard again to change the token.") });
            if (result.BackupFolder != null) Add(new Label { Text = T("Резервная копия заменённых файлов: ", "Backup of replaced files: ") + result.BackupFolder });
            var open = new Button { Text = T("Открыть рабочую папку", "Open working folder"), AutoSize = true };
            open.Click += delegate { try { Process.Start(new ProcessStartInfo(result.Folder) { UseShellExecute = true }); } catch { } };
            Add(open);
            install.Text = T("Готово", "Done");
            Add(install);
        }
    }

    static class Program
    {
        [STAThread]
        static void Main(string[] args)
        {
            bool own;
            using (var mutex = new Mutex(true, @"Local\HVPC-Resolve-MVSEP-Setup", out own))
            {
                if (!own) { MessageBox.Show("Мастер установки уже открыт. / Setup is already open.", SetupCore.Product); return; }
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                string source = AppDomain.CurrentDomain.BaseDirectory;
                if (args.Length == 2 && args[0] == "--source") source = args[1];
                string utility = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "Blackmagic Design", "DaVinci Resolve", "Support", "Fusion", "Scripts", "Utility");
                try { Application.Run(new SetupForm(source, utility)); }
                catch { MessageBox.Show("Не удалось открыть мастер установки. Распакуйте весь ZIP и повторите.\nSetup could not open. Extract the entire ZIP and try again.", SetupCore.Product); }
                finally { mutex.ReleaseMutex(); }
            }
        }
    }
}
