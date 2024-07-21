#!/usr/local/bin/python
import yt_dlp
import psutil
import sys
import threading
import getConfig
from pathlib import Path
import subprocess
import discord_web
from time import sleep, asctime

#id = sys.argv[1]
#id = "kJGsWORSg-4"
#outputFile = None
kill_all = False

def createTorrent(output):
    if not getConfig.getTorrent():
        return
    fullPath = getConfig.getTempOutputPath(output)
    folder = Path(fullPath).parent
    
    torrentRunner = subprocess.run(getConfig.torrentBuilder(fullPath,folder), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        
def delete_empty_folders(path):
    # Iterate through all files and directories in the specified directory
    for dir_path in path.rglob('*'):
        # Check if the current path is a directory
        if dir_path.is_dir() and not any(dir_path.iterdir()):
            # Remove directory if empty
            print("Removing path: {0}".format(dir_path))
            dir_path.rmdir()

def moveToFinal(output, members):
    from shutil import move
    source = getConfig.getTempOutputPath(output)
    if(members == True):
        dest = getConfig.getMembershipOutputPath(output)
    else:
        dest = getConfig.getDoneOutputPath(output)
    # Ensure the destination folder exists
    dest_path = dest.parent
    dest_path.mkdir(parents=True, exist_ok=True)

    # List all files in the source folder
    src_path = source.parent
    files = src_path.glob('*')
    print("Moving all {0} to {1}".format(source, dest))

    for file_path in files:
        # Build the destination path
        dest_file_path = dest_path / file_path.name

        # Move the file
        #file_path.rename(dest_file_path)
        move(file_path, dest_file_path)
        
    #Clean empty folders
    delete_empty_folders(Path(getConfig.getTempFolder()))

def compressChat(output):
    import zipfile
    partPath = Path("{0}.live_chat.json.part".format(getConfig.getTempOutputPath(output)))
    input_path = Path("{0}.live_chat.json".format(getConfig.getTempOutputPath(output)))
    output_path = Path("{0}.live_chat.zip".format(getConfig.getTempOutputPath(output)))

    if partPath.exists():
        from shutil import move
        move(partPath,input_path)

    try:
        with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as zipf:
            zipf.write(input_path, arcname=input_path.name)
        input_path.unlink()
        
    except Exception as e:
        print(e)
        raise Exception(("Unable to compress chat for id: {0}".format(id)))        

def chatBuilder(id, outputFile, useCookies=True):
    out = ["chat_downloader", "--logging", "critical"]
    cookies = getConfig.getCookiesFile()
    if cookies and useCookies:
        out += ["--cookies", cookies]
    out += ["-o", "{0}.live_chat.json".format(getConfig.getTempOutputPath(outputFile))]
    out.append("https://www.youtube.com/watch?v={0}".format(id))
    return out
    
def download_chat_ytdlp(video_url,outputFile):    
    options = {
        'wait_for_video': (1,15),
        'retries': 25,
        'skip_download': True,
        'outtmpl': getConfig.get_ytdlp(),
        'cookiefile': getConfig.getCookiesFile(),        
        'quiet': True,
        'no_warnings': True,
        'writesubtitles': True,
        'subtitleslangs': ['livechat'],
        'subtitlesformat': 'json',
        'live_from_start': True       
    }

    with yt_dlp.YoutubeDL(options) as ydl:
        ydl.download(video_url)
    return 0
"""
def download_chat(id,outputFile):
    if getConfig.getChat() and not getConfig.vid_Only():        
        try:
            chatRunner = subprocess.Popen(chatBuilder(id, outputFile), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            
            while chatRunner.poll() is None:
                if kill_all:
                    chatRunner.terminate()
                    break
                sleep(0.5)
        except subprocess.CalledProcessError as e:
            print(e.stderr)
            #If fail, try use without cookies
            print("Failed to run chatdownloader for {0} with cookies, trying without...".format(id))
            try:
                chatRunner = subprocess.Popen(chatBuilder(id, outputFile, False), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)   
                
                while chatRunner.poll() is None:
                    if kill_all:
                        chatRunner.terminate()
                        break
                    sleep(0.5)
            except:
                print("Downloading chat for {0} failed, trying once more with yt-dlp".format(id))
                try:
                    download_chat_ytdlp(id,outputFile)
                    try:
                        compressChat(outputFile)
                        return 0
                    except:
                        print("Compressing chat for {0} failed".format(id))
                        return 2 
                except:
                    print("Downloading chat for {0} failed".format(id))
                    return 1
        try:
            compressChat(outputFile)
            return 0
        except:
            print("Compressing chat for {0} failed".format(id))
            return 2 
    return 0
"""
def replace_ip_in_json(file_name):
    import re
    pattern = re.compile(r'((?:[0-9]{1,3}\.){3}[0-9]{1,3})|((?:[a-f0-9]{1,4}:){7}[a-f0-9]{1,4})')

    with open(file_name, 'r', encoding="utf8") as file:
        content = file.read()

    modified_content = re.sub(pattern, '0.0.0.0', content)

    with open(file_name, 'w', encoding="utf8") as file:
        file.write(modified_content)
        
def download_info(id,outputFile):
    #If marked vid_Only or none of the infos have been marked as true, skip downloading
    if (getConfig.vid_Only() or not
            (getConfig.getThumbnail() or 
             getConfig.getDescription() or 
             getConfig.getInfoJson() or
             getConfig.getChat())        
    ):
        return 0
    options = {
        'wait_for_video': (1,15),
        'writethumbnail': getConfig.getThumbnail(),
        'writedescription': getConfig.getDescription(),
        'writeinfojson': getConfig.getInfoJson(),
        'retries': 25,
        'skip_download': True,
        'outtmpl': str(getConfig.getTempOutputPath(outputFile)),
        'cookiefile': getConfig.getCookiesFile(),        
        'quiet': True,
        'no_warnings': True,
        'live_from_start': True,
    }
    
    if getConfig.getChat():
        options.update({
            'writesubtitles': True,
            'subtitleslangs': ['live_chat'],
            'subtitlesformat': 'json',
        })
    
    url = 'https://www.youtube.com/watch?v={0}'.format(id)
    with yt_dlp.YoutubeDL(options) as ydl:
        ydl.download(url)
        try:
            replace_ip_in_json(("{0}.info.json".format(outputFile)))
            
        except Exception as e:
            print("Unable to remove IP from info.json file for {0}".format(id))
            print(e)
            return 1
        try:
            compressChat(("{0}.live_chat.json".format(outputFile)))
            
        except Exception as e:
            print("Unable to compress live_chat file for {0}".format(id))
            print(e)
            return 1
    return 0

def downloader(id,outputTemplate, members):
    if id is None or outputTemplate is None:
        raise Exception(("Unable to retrieve information about video {0}".format(id)))
    #outputFile = "{0}{1}".format(getConfig.getTempFolder(),output)
    output = str(getConfig.getTempOutputPath(outputTemplate))
    
    # Start additional information downloaders
    discord_notify = threading.Thread(target=discord_web.main, args=(id, "recording"), daemon=True)
    #chat_downloader = threading.Thread(target=download_chat, args=(id,output), daemon=True)
    info_downloader = threading.Thread(target=download_info, args=(id,output), daemon=True)
    discord_notify.start()
    #chat_downloader.start()
    info_downloader.start()
    
    
    ytarchiveCMD = getConfig.ytarchiveBuilder(id,output)
    
    print("ytarchive for {0} starting with {1}".format(id,ytarchiveCMD))
    try:
        result = subprocess.run(ytarchiveCMD, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    except subprocess.CalledProcessError as e:
        print(e.stderr)
        discord_web.main(id, "error", message=str(e.stderr)[-1500:])
        global kill_all
        kill_all = True
        sleep(1.0)
        raise Exception(("{2} - Error downloading video: {0}, Code: {1}".format(id, e.returncode, asctime())))
        return
    # Wait for remaining processes
    discord_notify.join()
    #chat_downloader.join()
    info_downloader.join()
        
    if(getConfig.getTorrent()):
        try:
            createTorrent(outputTemplate)
        except subprocess.CalledProcessError as e:
            print(e.stderr)
            discord_web.main(id, "error", message=str(e.stderr)[-1500:])
            raise Exception(("Error creating torrent for video: {0}, Code: {1}".format(id, e.returncode)))
        
    print("{0} finished successfully".format(id))
    moveToFinal(outputTemplate, members)
    discord_web.main(id, "done")
    return

def download_video_info(video_url):
    options = {
        'wait_for_video': (1,300),
        'retries': 25,
        'skip_download': True,
        'outtmpl': getConfig.get_ytdlp(),
        'cookiefile': getConfig.getCookiesFile(),        
        'quiet': True,
        'no_warnings': True       
    }

    with yt_dlp.YoutubeDL(options) as ydl:
        info_dict = ydl.extract_info(video_url, download=False)
        outputFile = str(ydl.prepare_filename(info_dict))
        if(info_dict.get('availability', None) == 'subscriber_only' and getConfig.membership_directory() is not None):
            members = True
        else:
            members = False
            
        
    print("Output file: {0}".format(outputFile))
    return outputFile, members

def is_script_running(script_name, id):
    current = psutil.Process()
    #print("PID: {0}, command line: {1}, argument: {2}".format(current.pid, current.cmdline(), current.cmdline()[2:]))
    current_pid = psutil.Process().pid
    
    for process in psutil.process_iter():
        try:
            process_cmdline = process.cmdline()
            if (
                process.pid != current_pid and
                script_name in process_cmdline and
                id in process_cmdline[2:]   # Needs testing between Windows and Postix to ensure compatibility
            ):
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return False
    
def main(id=None):
    script_name = sys.argv[0]
    # If id was no included as a variable, try and retrieve from sys args
    if id is None:
        try:
            id = sys.argv[1]
        except:
            raise Exception("No video ID provided, unable to continue")
    # If system args were also none, raise exception
    if id is None:
        raise Exception("No video ID provided, unable to continue")
    if is_script_running(script_name, id):
        #print("{0} already running, exiting...".format(id))
        return 0
    
    discord_web.main(id, "waiting")
    outputFile, members = download_video_info(id)
    #print("Output file: {0}".format(outputFile))
    if outputFile is None:
        discord_web.main(id, "error")
        raise Exception(("Unable to retrieve information about video {0}".format(id)))
    
    downloader(id,outputFile, members)

if __name__ == "__main__":
    main()
