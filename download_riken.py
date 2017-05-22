import requests
import argparse
import sys, os
import re
import threading
import time
from queue import Queue

def test_access(path):
	res = {"r":True,"w":True}
	testfile = path + "87896456453325649876546"
	try:
		f = open(testfile,"w")
		f.write("write test\t\n\n")
		f.close()
	except Exception as e:
		res["w"]  = False
		os.remove(testfile)
	try:
		f = open(testfile,"r")
		s = f.read().splitlines()
		f.close()
	except Exception as e:
		res["r"]  = False
	os.remove(testfile)
	return res

def files_lister(rikendirs,dlq,rootpath,ftypes):
	pat = re.compile("\d+_\d*")
	fnptn = re.compile("\d*\..*")
	for i, rd in enumerate(rikendirs):
		try:
			ldir = re.search(pat,rd).group()
			for ft in ftypes:
				if not os.path.exists(rootpath + ldir + "/" + ft):
					os.makedirs(rootpath + ldir + "/" + ft)
			localfiles = list_localfiles(directory=rootpath + ldir + "/",filetypes=ftypes)
			ldirfns = list_rikenfiles(directory=rd, filetypes=ftypes, localfiles=localfiles)				
			for ft in ftypes:
				fcount[ft] += len(ldirfns[ft])
				for fn in ldirfns[ft]:
					tgt = re.search(fnptn,fn).group()
					dlq.put({"url":fn,"path":ldir+"/"+ft+"/"+tgt})
		except Exception as e:
			print("Error: ", end = "")
			print(e, end = "\n")

def download_threadfunct(filequeue,rootpath):
	headers = { 'User-Agent': 'Mozilla/5.0 (Windows NT 6.0; WOW64; rv:24.0) Gecko/20100101 Firefox/24.0'}
	sess = requests.session()
	while True:
		try:
			d = filequeue.get()
			response = sess.get("http://pubchemqc.riken.jp/" + d["url"])
			if response.ok:
				with open(rootpath+d["path"],"wb") as f:
					f.write(response.content)
			filequeue.task_done()
		except Exception as e:
			print("Error downloading file: " + d["url"], file=sys.stderr)
			print(e, file=sys.stderr)
			print("Restarting session...", file=sys.stderr)
			sess = requests.session()
			filequeue.put(d)

def download_http(url):
	sess = requests.session()
	try:
		response = sess.get(url)
		if response.ok:
			return response.content.decode("UTF-8")
		else:
			return None
	except Exception as e:
		print("Exception in download_http:")
		print(e, file=sys.stderr)

def get_riken_index():
	dirpattern = re.compile("Compound.*\.html")
	countpattern = re.compile("(?<=Currently )\d*(?= molecules are available on this site)")
	indexhtml = download_http("http://pubchemqc.riken.jp/")
	dirs = re.findall(dirpattern, indexhtml)
	nbmols = (int)(re.search(countpattern,indexhtml).group())
	return dirs, nbmols
	
def list_rikenfiles(directory, filetypes, localfiles):
	filecontent = download_http("http://pubchemqc.riken.jp/" + directory)
	fndict = {}
	if "log" in filetypes:
		logptn = re.compile("(?<=href\=\")Compound.*.*b3lyp_6\-31g\(d\)\.log\.xz(?=\")")
		fndict["log"] = re.findall(logptn, filecontent)
	if "tdlog" in filetypes:
		tdlogptn = re.compile("(?<=href\=\")Compound.*td.*\.log\.xz(?=\")")
		fndict["tdlog"] = re.findall(tdlogptn, filecontent)
	if "mol" in filetypes:
		molptn = re.compile("(?<=href\=\")Compound.*\.mol(?=\")")
		fndict["mol"] = re.findall(molptn, filecontent)
	ptn = re.compile("(?<=\/)\d*\..*")
	for ft in fndict:
		torm = []
		for fn in fndict[ft]:
			localfn = re.search(ptn,fn).group()
			if localfn in localfiles:
				torm.append(fn)
				localfiles.remove(localfn)
		fndict[ft] = [fn for fn in fndict[ft] if fn not in torm]
	return fndict

def list_localfiles(directory,filetypes):
	res = []
	for root, dirs, files in os.walk(directory):
		for file in files:
			for ft in filetypes:			
				if file.endswith(ft):
					res.append(file)
	return res


if __name__ == '__main__':
	"""
	Args parsing
	"""
	parser = argparse.ArgumentParser()
	parser.add_argument("--log", help="Download .log files", action="store_true")
	parser.add_argument("--tdlog", help="Download .td.log files", action="store_true")
	parser.add_argument("--mol", help="Download .mol files", action="store_true")
	parser.add_argument("target", help="Folder that will contain the downloaded content")
	args = parser.parse_args()	
	if not (args.log or args.tdlog or args.mol):
		print("Error: no files to download, please use at least one of {--log, --tdlog, --mol} arguments.",file=sys.stderr)
		sys.exit(1)
	rootpath = args.target + "/" if args.target[-1] != "/" else args.target
	pathrights = test_access(rootpath)
	if not (pathrights["r"] or pathrights["w"]):
		print("Error: Cannot read or write to path " + rootpath,file=sys.stderr)
		print("Please correct this or chose another path.",file=sys.stderr)
		sys.exit(1)
	ftypes = []
	fcount = {}
	if args.log:
		ftypes.append("log")
		fcount["log"] = 0
	if args.tdlog:
		ftypes.append("tdlog")
		fcount["tdlog"] = 0
	if args.mol:
		ftypes.append("mol")
		fcount["mol"] = 0

	print("Getting Riken index...")
	rikendirs, nbmols = get_riken_index()
	dlq = Queue()	
	print("Starting files listing thread...")
	flt = threading.Thread(target=files_lister, args=(rikendirs,dlq,rootpath,ftypes))
	flt.daemon = True
	flt.start()

	while dlq.qsize() == 0:
		print("Waiting for first downloads to start...", end = "\r")
		time.sleep(0.3)

	print("Starting download threads...")
	threads = []
	for i in range(3):
		t = threading.Thread(target=download_threadfunct, args=(dlq,rootpath))
		threads.append(t)
		t.daemon = True
		t.start()

	start = time.time()
	print("Downloads started.")
	while dlq.qsize() > 0:
		nbfiles = dlq.qsize()
		now = time.time()
		percentdone = 100 * (nbfiles-dlq.qsize()) / nbfiles 
		print(str(percentdone)[:4] + "% done in " + str(int(now-start)) + " seconds, " + str(dlq.qsize()) + " files left to download.", end = "\r")
		time.sleep(1)
	print("Downloads finished. Quitting.")
	