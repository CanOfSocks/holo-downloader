import psutil
import subprocess
import getConfig
import sys
import os
from shutil import move
import discord_web
from json import load

def run_yta_raw(json_file, output_path = None, ytdlp_json = None):
    data = None
    with open(json_file, 'r', encoding='utf-8') as file:
            # Load the JSON data from the file
            data = load(file)
    if data:
        discord_web.main(data['metadata']['id'], "recording")
    if os.name == 'nt':
        command = ['ytarchive-raw-go-windows-amd64']
    else:
        command = ['ytarchive-raw-go']
    command += [ '--threads', '20', '--overwrite-temp', '-i', json_file, '-v']
    if getConfig.getUnarchivedTempFolder():
        command += ['--temp-dir', str(getConfig.getUnarchivedTempFolder())]
    #if not getConfig.getMux():
    #    command += ["--merger", "download-only"]
    if output_path:
        output = output_path
    elif getConfig.getOutputTemplateYTAraw():
        output = ['--output', os.path.join(getConfig.getUnarchivedFolder(), getConfig.get_ytdlp)]
    else:
        output = ['--output', os.path.join(getConfig.getUnarchivedFolder(), '[%(upload_date)s] %(title)s [%(channel)s] (%(id)s)')]
    command += ['--output', output]
    #print(' '.join(command))
    try:
        
        result = subprocess.run(command, check=True, text=True)
    except subprocess.CalledProcessError as e:
        #print(e.stdout.decode())
        #print(e.stderr.decode())
        discord_web.main(data['metadata']['id'], "error")
        raise Exception(("Error downloading unarchived video, Code: {0}".format(e.returncode)))
    if result.returncode == 0:
        os.remove(json_file)
        if ytdlp_json and output_path:
            move(ytdlp_json, '{0}.info.json'.format(output))
        if data:
            discord_web.main(data['metadata']['id'], "done")
        
        
        
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
    
def main(json_file = None, output = None, ytdlp_json = None):
    script_name = sys.argv[0]
    # If id was no included as a variable, try and retrieve from sys args
    if json_file is None:
        try:
            json_file = sys.argv[1]
        except:
            raise Exception("No video JSON file provided, unable to continue")
    if output is None:
        try:
            output = sys.argv[2]
        except:
            pass
    if ytdlp_json is None:
        try:
            ytdlp_json = sys.argv[3]
        except:
            raise Exception("No video JSON file provided, unable to continue")
    # If system args were also none, raise exception
    if json_file is None:
        raise Exception("No video JSON file provided, unable to continue")
    if is_script_running(script_name, json_file):
        #print("{0} already running, exiting...".format(id))
        return 0
    run_yta_raw(json_file, output, ytdlp_json)


if __name__ == "__main__":
    main()