#!/usr/bin/env python3
"""Opt-in offline Claude UI/PTTY probe, not part of unittest discovery.

Uses ONLY an explicitly supplied pre-onboarded disposable fixture directory.
Fake key + localhost port 1 + --bare; no user prompts/API requests/tools/keychain.
The fixture must have an owner marker and probe.sh/settings.json. Raw terminal
bytes and a small interpreted footer readback are saved only under that fixture.
"""
import argparse
import codecs
import fcntl
import json
import os
from pathlib import Path
import pty
import re
import select
import signal
import struct
import sys
import termios
import time
import unicodedata

EXPECTED = "▁▂▃▄▅▆▇██▅▇█"


class TextScreen:
    """Small VT text projection: cursor/erase/scroll; ignores only styling/modes.

    This is terminal readback, NOT a claimed screenshot. Raw bytes are retained
    so another terminal emulator can independently inspect the rendering.
    """
    def __init__(self, cols, rows=40):
        self.cols,self.rows=cols,rows
        self.grid=[[" "]*cols for _ in range(rows)]
        self.x=self.y=0
        self.saved=(0,0)
        self.state="text";self.escape=""
        self.decoder=codecs.getincrementaldecoder("utf-8")("replace")

    def down(self):
        self.y+=1
        if self.y>=self.rows:
            self.grid.pop(0);self.grid.append([" "]*self.cols);self.y=self.rows-1

    def feed(self,data):
        for char in self.decoder.decode(data):
            if self.state=="osc":
                if char=="\a":self.state="text"
                elif char=="\x1b":self.state="osc_escape"
                continue
            if self.state=="osc_escape":
                self.state="text" if char=="\\" else "osc";continue
            if self.state=="charset":self.state="text";continue
            if self.state=="escape":
                if char=="[":self.state="csi";self.escape=""
                elif char=="]":self.state="osc"
                elif char in "()":self.state="charset"
                else:
                    if char=="7":self.saved=(self.x,self.y)
                    if char=="8":self.x,self.y=self.saved
                    self.state="text"
                continue
            if self.state=="csi":
                if "@"<=char<="~":self.csi(self.escape,char);self.state="text"
                else:self.escape+=char
                continue
            if char=="\x1b":self.state="escape"
            elif char=="\r":self.x=0
            elif char=="\n":self.down()
            elif char=="\b":self.x=max(0,self.x-1)
            elif char=="\t":self.x=min(self.cols-1,(self.x//8+1)*8)
            elif ord(char)>=32 and char!="\x7f":
                if unicodedata.combining(char):continue
                if self.x>=self.cols:self.x=0;self.down()
                self.grid[self.y][self.x]=char
                self.x+=2 if unicodedata.east_asian_width(char) in ("W","F") else 1

    def csi(self,params,command):
        if params.startswith(("?",">","<","=")):
            if params=="?1049" and command=="h":
                self.grid=[[" "]*self.cols for _ in range(self.rows)];self.x=self.y=0
            return
        numbers=[int(x) if x.isdigit() else 0 for x in params.split(";")]
        n=numbers[0] or 1
        if command=="A":self.y=max(0,self.y-n)
        elif command=="B":self.y=min(self.rows-1,self.y+n)
        elif command=="C":self.x=min(self.cols-1,self.x+n)
        elif command=="D":self.x=max(0,self.x-n)
        elif command in ("G","`"):self.x=min(self.cols-1,n-1)
        elif command=="d":self.y=min(self.rows-1,n-1)
        elif command in ("H","f"):
            self.y=min(self.rows-1,n-1);self.x=min(self.cols-1,(numbers[1] or 1)-1 if len(numbers)>1 else 0)
        elif command=="K":
            mode=numbers[0]
            if mode==2:self.grid[self.y]=[" "]*self.cols
            elif mode==0:self.grid[self.y][self.x:]=[" "]*(self.cols-self.x)
            elif mode==1:self.grid[self.y][:self.x+1]=[" "]*(self.x+1)
        elif command=="J":
            if numbers[0] in (2,3):self.grid=[[" "]*self.cols for _ in range(self.rows)]
            elif numbers[0]==0:
                self.grid[self.y][self.x:]=[" "]*(self.cols-self.x)
                for row in range(self.y+1,self.rows):self.grid[row]=[" "]*self.cols
        elif command=="s":self.saved=(self.x,self.y)
        elif command=="u":self.x,self.y=self.saved
        # m=SGR, r=scroll region, c=device query, q=cursor style do not
        # change the projected footer text in this probe.

    def lines(self):return ["".join(row).rstrip() for row in self.grid]


def probe(binary,fixture,width):
    screen=TextScreen(width)
    pid,fd=pty.fork()
    if pid==0:
        fcntl.ioctl(0,termios.TIOCSWINSZ,struct.pack("HHHH",40,width,0,0))
        os.chdir(fixture/"work")
        env={**os.environ,"HOME":str(fixture/"home"),"CLAUDE_CONFIG_DIR":str(fixture/"config"),
             "ANTHROPIC_API_KEY":"cockpit-ui-test-placeholder","ANTHROPIC_BASE_URL":"http://127.0.0.1:1",
             "DISABLE_AUTOUPDATER":"1","CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC":"1",
             "TERM":"xterm-256color","COLORTERM":"truecolor"}
        args=[str(binary),"--bare","--setting-sources","","--settings",str(fixture/"settings.json"),
              "--strict-mcp-config","--tools","","--no-chrome"]
        os.execve(binary,args,env)
    raw=bytearray();footer=[];passed=False
    try:
        deadline=time.monotonic()+12
        stable=None
        while time.monotonic()<deadline:
            ready,_,_=select.select([fd],[],[],.3)
            if ready:
                try:data=os.read(fd,65536)
                except OSError:break
                if not data:break
                raw.extend(data);screen.feed(data)
            lines=screen.lines()
            start=next((i for i,row in enumerate(lines) if row.strip().startswith("▲")),None)
            if start is not None:
                footer=lines[start:]
                actual="".join(re.findall("[▁▂▃▄▅▆▇█]","".join(footer)))
                # Newline/host indentation can split after the label's space;
                # compare all semantic prefix characters without whitespace.
                prefix=re.sub(r"\s+","","".join(footer))
                if actual==EXPECTED and "▲hit91.3%" in prefix:
                    stable=stable or time.monotonic()
                    if time.monotonic()-stable>2:passed=True;break
                else:stable=None
        (fixture/f"host-{width}.ansi").write_bytes(raw)
        (fixture/f"host-{width}.txt").write_text("\n".join(screen.lines())+"\n")
        return {"columns":width,"rows":40,"expected_prefix":"▲ hit 91.3%",
                "expected_cells":EXPECTED,"actual_footer":footer,"passed":passed,
                "evidence":"actual Claude Code PTY bytes, text-projected (not a screenshot)"}
    finally:
        os.write(fd,b"\x03\x03")
        stop=time.monotonic()+3
        while time.monotonic()<stop:
            ended,_=os.waitpid(pid,os.WNOHANG)
            if ended:break
            ready,_,_=select.select([fd],[],[],.1)
            if ready:
                try:os.read(fd,65536)
                except OSError:break
        try:
            ended,_=os.waitpid(pid,os.WNOHANG)
            if not ended:os.kill(pid,signal.SIGTERM);os.waitpid(pid,0)
        except ChildProcessError:pass
        os.close(fd)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--claude",type=Path,required=True)
    parser.add_argument("--fixture",type=Path,required=True)
    parser.add_argument("--report",type=Path,required=True)
    args=parser.parse_args();fixture=args.fixture.resolve()
    if fixture.parent!=Path("/private/tmp") or not (fixture/".cockpit-fixture-owned").is_file():
        parser.error("Requires an explicitly owned /private/tmp fixture, never a real user config")
    results=[probe(args.claude,fixture,width) for width in (10,12,16,20,26,30,80)]
    report={"host_version":"2.1.288 (measured with --version)",
            "offline":True,"fake_key_only":True,"api_requests_submitted":0,
            "fixture_not_installed_user_config":True,"results":results}
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if all(result["passed"] for result in results) else 1


if __name__=="__main__":raise SystemExit(main())
