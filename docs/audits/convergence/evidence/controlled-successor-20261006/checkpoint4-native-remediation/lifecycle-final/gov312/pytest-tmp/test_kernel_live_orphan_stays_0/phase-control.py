
import os,time
if os.fork()==0:
    time.sleep(30)
    os._exit(0)
os._exit(0)
