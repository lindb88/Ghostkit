// A small IRC bot in Go, with nothing but Go's standard library. Test it with irclab:
//
//	python3 -m irclab run examples/go/hellobot.scenario
//
// Run it for real:  go run hellobot.go -server irc.libera.chat -port 6667 -channel '#mytest'
// With a status page: add -status www/status.json, then: python3 -m webstatus serve www
package main

import (
	"bufio"
	"encoding/json"
	"flag"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

var conn net.Conn

// ---- the status page (webstatus): who is where, written to -status every 30 seconds ----

type channel struct {
	name       string
	nicks, ops map[string]bool // lower case
}

var (
	mu        sync.Mutex
	chans     = map[string]*channel{}
	me        string
	server    string
	statusTo  string
	connected bool
	since     int64
	started   = time.Now().Unix()
)

type chanStatus struct {
	Name  string `json:"name"`
	Users int    `json:"users"`
	Op    bool   `json:"op"`
}

type botStatus struct {
	Name      string       `json:"name"`
	Connected bool         `json:"connected"`
	Server    string       `json:"server"`
	Started   int64        `json:"started"`
	Since     int64        `json:"since,omitempty"`
	Version   string       `json:"version"`
	Channels  []chanStatus `json:"channels"`
}

func writeStatus() {
	if statusTo == "" {
		return
	}
	mu.Lock()
	b := botStatus{Name: me, Connected: connected, Server: server, Started: started, Version: "1.0",
		Channels: []chanStatus{}}
	if connected {
		b.Since = since
	}
	for _, c := range chans {
		b.Channels = append(b.Channels, chanStatus{c.name, len(c.nicks), c.ops[strings.ToLower(me)]})
	}
	mu.Unlock()
	data, _ := json.Marshal(map[string]interface{}{"format": 1, "updated": time.Now().Unix(),
		"bots": []botStatus{b}})
	tmp := filepath.Join(filepath.Dir(statusTo), fmt.Sprintf(".status-%d.tmp", os.Getpid()))
	if os.WriteFile(tmp, data, 0o644) == nil {
		os.Rename(tmp, statusTo) // the page never reads half a file
	}
}

func gone(c *channel, who string) {
	delete(c.nicks, strings.ToLower(who))
	delete(c.ops, strings.ToLower(who))
}

func track(cmd, who string, args []string, text string) {
	mu.Lock()
	low := strings.ToLower
	arg := func(i int) string {
		if i < len(args) {
			return args[i]
		}
		return ""
	}
	write := false
	switch cmd {
	case "001":
		connected, since, me, write = true, time.Now().Unix(), arg(1), true
	case "353": // NAMES: <me> = #chan :@op +voice nick
		if c := chans[low(arg(3))]; c != nil {
			for _, n := range strings.Fields(text) {
				name := low(strings.TrimLeft(n, "@+%&~"))
				c.nicks[name] = true
				if strings.HasPrefix(n, "@") {
					c.ops[name] = true
				}
			}
		}
	case "366":
		write = true
	case "JOIN":
		name := arg(1)
		if name == "" {
			name = text
		}
		if low(who) == low(me) {
			chans[low(name)] = &channel{name, map[string]bool{}, map[string]bool{}}
			write = true
		} else if c := chans[low(name)]; c != nil {
			c.nicks[low(who)] = true
		}
	case "PART", "KICK":
		gonewho := who
		if cmd == "KICK" {
			gonewho = arg(2)
		}
		if c := chans[low(arg(1))]; c != nil {
			if low(gonewho) == low(me) {
				delete(chans, low(arg(1)))
			} else {
				gone(c, gonewho)
			}
		}
	case "QUIT":
		for _, c := range chans {
			gone(c, who)
		}
	case "NICK":
		to := low(text)
		if to == "" {
			to = low(arg(1))
		}
		for _, c := range chans {
			if c.nicks[low(who)] {
				delete(c.nicks, low(who))
				c.nicks[to] = true
			}
			if c.ops[low(who)] {
				delete(c.ops, low(who))
				c.ops[to] = true
			}
		}
		if low(who) == low(me) {
			me = to
		}
	case "MODE": // +o/-o, one at a time or several: +oo a b
		if c := chans[low(arg(1))]; c != nil {
			rest := append(append([]string{}, args[2:]...), strings.Fields(text)...)
			if len(rest) > 0 {
				sign, i := '+', 1
				for _, m := range rest[0] {
					if m == '+' || m == '-' {
						sign = m
						continue
					}
					if strings.ContainsRune("ovbkIeh", m) || (m == 'l' && sign == '+') {
						if i < len(rest) && m == 'o' {
							if sign == '+' {
								c.ops[low(rest[i])] = true
							} else {
								delete(c.ops, low(rest[i]))
							}
						}
						i++
					}
				}
			}
		}
	}
	mu.Unlock()
	if write {
		writeStatus()
	}
}

// send never lets text from IRC become a second line: no CR, LF or NUL
func send(line string) {
	line = strings.NewReplacer("\r", " ", "\n", " ", "\x00", " ").Replace(line)
	fmt.Fprintf(conn, "%s\r\n", line)
}

func say(target, text string) { send("PRIVMSG " + target + " :" + text) }

func main() {
	host := flag.String("server", "127.0.0.1", "IRC server")
	port := flag.Int("port", 6667, "port")
	nick := flag.String("nick", "HelloBot", "nick")
	channel := flag.String("channel", "#test", "channel to join")
	status := flag.String("status", "", "write status.json for webstatus here")
	flag.Parse()
	me, server, statusTo = *nick, *host, *status
	go func() {
		for range time.Tick(30 * time.Second) {
			writeStatus()
		}
	}()
	defer func() { connected = false; writeStatus() }()

	var err error
	conn, err = net.Dial("tcp", fmt.Sprintf("%s:%d", *host, *port))
	if err != nil {
		fmt.Println("connect:", err)
		return
	}
	send("NICK " + *nick)
	send("USER " + strings.ToLower(*nick) + " 0 * :hellobot in Go")

	r := bufio.NewScanner(conn)
	for r.Scan() {
		line := strings.TrimRight(r.Text(), "\r")
		prefix := ""
		if strings.HasPrefix(line, ":") { // :nick!user@host COMMAND params :text
			sp := strings.IndexByte(line, ' ')
			if sp < 0 {
				continue
			}
			prefix, line = line[1:sp], line[sp+1:]
		}
		text := ""
		if i := strings.Index(line, " :"); i >= 0 {
			line, text = line[:i], line[i+2:]
		}
		parts := strings.Fields(line)
		if len(parts) == 0 {
			continue
		}
		who := strings.SplitN(prefix, "!", 2)[0]
		track(parts[0], who, parts, text)
		switch parts[0] {
		case "PING":
			send("PONG :" + text)
		case "376", "422": // the end of the welcome
			send("JOIN " + *channel)
		case "PRIVMSG":
			if len(parts) < 2 {
				continue
			}
			where := parts[1]
			if !strings.HasPrefix(where, "#") {
				where = who // privately back
			}
			words := strings.Fields(text)
			if len(words) == 0 {
				continue
			}
			cmd := strings.ToLower(words[0])
			if where == who && !strings.HasPrefix(cmd, "!") {
				cmd = "!" + cmd // privately, the ! may be left out
			}
			switch cmd {
			case "!hello":
				say(where, "Hello, "+who+"!")
			case "!echo":
				if len(words) > 1 {
					say(where, strings.Join(words[1:], " "))
				} else {
					say(where, "Echo what?")
				}
			}
		}
	}
}
