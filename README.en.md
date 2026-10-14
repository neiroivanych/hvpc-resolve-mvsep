# HVPC Resolve-MVSEP

[Русский](README.md) · **English**

Separate sound from audio and video files and add the results to DaVinci Resolve Studio — to the Media Pool or onto separate audio tracks. The interface is available in Russian and English.

**Independent, unofficial integration.** HVPC Resolve-MVSEP is not affiliated with or endorsed by Blackmagic Design or the operator of MVSEP. The names DaVinci Resolve and MVSEP identify compatibility and belong to their respective owners.

## Features

- MVSEP algorithm catalog with groups, search, model selection, and settings.
- Russian and English interface.
- WAV, MP3, FLAC, and MP4 input with audio. MVSEP extracts the audio from MP4 on its server.
- Background processing, queue status, and recovery of saved tasks after a failure.
- Import into Media Pool or onto separate audio tracks, using the source clip's current position and trim.
- Named presets and remembered settings.

## Requirements

- Windows and **DaVinci Resolve Studio 21.1 or later**. Tested with **21.1.0.17**; operation on other versions is not guaranteed.
- An MVSEP account, your own API token, and an internet connection.
- Write access to the application and results folders.

The application uses the Python runtime included with Resolve 21.1+. No separate Python, FFmpeg, or additional libraries are required. The free edition of Resolve, macOS, and Linux are not supported in this release.

## Installation

1. Open [Releases](https://github.com/neiroivanych/hvpc-resolve-mvsep/releases), under **Assets** download **HVPC-Resolve-MVSEP-v0.3.0-Windows.zip** and fully extract it.
2. Wait for separations to finish, then close Resolve and HVPC Resolve-MVSEP, including its tray window. Run **Install.exe** from the extracted folder.
3. Choose a **Working folder**. Keep the suggested location or choose a new empty folder for the application, settings and results.
4. Paste your **MVSEP API token**. If you need one, follow the link in the wizard, sign in on the [MVSEP API page](https://mvsep.com/en/full_api) and copy your token. Click **Install**.
5. In Resolve, open **Preferences → System → General**. Set the first field, **External scripting using**, to **Local**, save and restart Resolve. If **Local** is already selected, no change is needed. The wizard reminds you on its final screen.

You can now use **Workspace → Scripts → HVPC Resolve-MVSEP**. There is no need to copy files into system folders or manually edit JSON or Python files. Administrator rights are not required.

The wizard supports Resolve Studio 21.1+ in the default `Program Files\Blackmagic Design\DaVinci Resolve` folder. It installs for the current Windows user. The token is stored locally in the working folder; your account password is not needed. The MVSEP connection is checked when the separation window opens.

The working folder determines the mode automatically: the button reads **Install** for a new folder and **Update / configure** for an existing installation.

To install the application, choose the archive bearing the application name and **Windows**. The **Source code** archives are intended for developers.

## Your first separation

1. Open a project and timeline. Select **one clip on the timeline**, rather than in Media Pool. For MP4, you may select a linked video/audio pair from the same file or either component on its own.
2. Run **Workspace → Scripts → HVPC Resolve-MVSEP**. Choose **RU** or **EN** under **Язык / Language** if needed.
3. Choose a group, algorithm, settings, and output format. Select a results folder or keep the displayed location.
4. Optionally enable **Add to project media** or **Add to tracks (and media)**. The selected action runs after downloading. These checkboxes apply only to the current separation.
5. Click **Start separation**. The results window opens when processing finishes.

**The entire source file is uploaded to MVSEP, including portions outside the timeline trim.** For MP4, the complete video file is uploaded. The source file on disk is not modified. Only submit material that you have the right to send to the service.

## Adding results to Resolve

| Button | What it does |
| --- | --- |
| **To Media Pool** | Adds the complete result files to the project for further editing. |
| **To new tracks** | Adds the files to Media Pool and places each component on a separate audio track at the bottom of the original timeline. |
| **Open results** | Opens the folder containing the downloaded files. |

Placement uses the **source clip's current position and current left and right trims**. You can move the same clip along the original timeline, including to another track. Result files remain complete: you can extend the edges of the new clips back into the available audio.

New tracks are initially **muted with M**. To listen, enable the desired result tracks and mute the original. The script does not change the original track's volume or enabled state. The results window shows the added track numbers; scroll down through the audio tracks if needed.

Clicking again checks existing files and clips without creating duplicates. If you move or trim the source after importing, existing result clips do not automatically follow it.

Placement stops with an explanation if the source clip was replaced or removed, the source file contents changed, the speed is unsupported, or another placement requirement is not met. Downloaded results remain available for Media Pool import. Placement supports ordinary mono/stereo audio at 100% speed; effects and complex speed changes are not transferred from the source clip.

## Where results are saved

The default destination is the `output` folder inside the application folder. Its full path is shown before starting. Use **Choose folder…** to change it and **Use for future separations** to remember it.

Each source and separation attempt has its own folder. Files from the same attempt share a variant number. **Open results** in the completed task window takes you directly to that task's files.

## Background processing and reopening results

While processing, **Run in background** or the window's close button hides the window and leaves the **HVPC Resolve-MVSEP** icon near the Windows clock. It may be inside the **↑** menu. Double-click it to reopen the window. Running the script again from Resolve also shows the active task without another upload.

Closing Resolve does not stop processing or downloading while the background process and computer remain running. Import becomes available after you reopen the original project and restore the connection. Click **Check connection** if needed. After a failed automatic import, use the import buttons manually.

The full window returns when the task completes or fails. If Windows prevents it from taking focus, look for its flashing taskbar button.

If you have already closed the completed task window, run the script and click **Latest result**. This reopens the project's latest completed result without another separation. Manual import buttons remain available even if you did not select automatic import before starting.

Shutting down the computer or forcibly stopping the background process interrupts local work. If a saved task offers **Resume task** or **Resume download**, that button continues it without uploading the source again. If the upload outcome is uncertain, first check your MVSEP history: the server may already have accepted the file.

## Settings, models, and limits

**My presets** stores an algorithm, its parameters, and the output format under a name of your choice. **Settings** in the results window shows the settings used for that attempt.

**Remember last settings** restores the algorithm, its group, parameters, and output format on the next launch. **Successful only** uses settings from a completed download; **All** uses your last selection, including cancelled separations. Turning it off restores the initial settings on the next launch.

**[Premium]** marks an algorithm or a particular option value. **Premium options available** means the algorithm includes paid options, even if its defaults are free. Check access and balance in your MVSEP account before submitting. The application does not provide a subscription or credits.

Balance and remaining free separations are displayed using MVSEP data. Unavailable data does not mean a zero balance or quota. The catalog, model availability, and limits depend on the service. Speech recognition, voice synthesis, and MIDI are not currently supported in this audio separation window; a subscription does not add their support to the application.

## Troubleshooting

| Situation | What to do |
| --- | --- |
| Menu entry is missing | Close Resolve, run the wizard again and select your working folder. Then reopen Resolve. |
| Application not found | Run the wizard again and select your existing working folder. Do not move it after installation. |
| A clip report opens instead of separation settings | Set `"action": "separate"` in `settings.json`, or use the current `settings.example.json` as your starting point. |
| No clip selected | Select one timeline clip; for MP4, a linked video/audio pair is accepted. |
| API token error | Close Resolve and the app windows, run the wizard, select your existing working folder and paste a valid token. |
| No Resolve connection | Open the original project, check **External scripting using → Local**, and click **Check connection**. |
| Track placement refused | Read the reason in the highlighted details area. Open the required timeline, restore the source, or use **To Media Pool**. |
| Tracks were added but there is no sound | Find the reported track numbers at the bottom of the timeline and enable them with **M**. |
| Download failure or timeout | Use **Resume download / Resume task** if available. |

For a reproducible problem, open an issue in this repository's **Issues** tab on GitHub. Include your Windows and Resolve versions, the application version or download date, source format, steps, and error message. Screenshots help if they contain no private information.

Do not attach your API token, the `config` folder, or the entire project. Before sharing a log from `logs` or a task folder, check it for filenames, local paths, and project names.

## Updating

Wait for background tasks to finish, then close Resolve and the application windows. Extract the new ZIP into a separate folder and run **Install.exe**. Select your previous installation as the **Working folder**; the wizard will usually find it automatically. Leave the token field empty to keep the saved token and click **Update / configure**.

Settings, tasks and results are kept. Replaced files are backed up in `config\installer-backups` inside the working folder. If writing fails, the wizard attempts to restore the previous files and reports the outcome.

To change your token, run the same wizard with your existing working folder. A wizard-installed menu entry always opens its selected folder, even if you previously set `RESOLVE_MVSEP_HOME`.

## License and third-party services

The original HVPC Resolve-MVSEP code is distributed under the [MIT license](LICENSE). This license grants no rights to DaVinci Resolve, MVSEP, their brands, models, or third-party descriptions. The application does not include the Resolve SDK or binaries, or model weights.

Processing is performed by MVSEP using your account and is governed by the [MVSEP terms](https://mvsep.com/en/terms). Algorithm descriptions are provided by MVSEP; Russian translations of selected passages were prepared for this integration and are not official MVSEP translations.
