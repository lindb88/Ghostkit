// A small IRC bot in Go, with nothing but Go's standard library. Test it with irclab:
//   python3 -m irclab run examples/go/hellobot.scenario
// Run it for real:  go run hellobot.go -server irc.libera.chat -port 6667 -channel '#mytest'
package main

import (
	"bufio"
	"flag"
	"fmt"
	"net"
	"strings"
)

var conn net.Conn

// send never lets text from IRC become a second line: no CR, LF or NUL
func send(line string) {
	line = strings.NewReplacer("\r", " ", "\n", " ", "\x00", " ").Replace(line)
	fmt.Fprintf(conn, "%s\r\n", line)
}

func say(target, text string) { send("PRIVMSG " + target + " :" + text) }

func main() {
	server := flag.String("server", "127.0.0.1", "IRC server")
	port := flag.Int("port", 6667, "port")
	nick := flag.String("nick", "HelloBot", "nick")
	channel := flag.String("channel", "#test", "channel to join")
	flag.Parse()

	var err error
	conn, err = net.Dial("tcp", fmt.Sprintf("%s:%d", *server, *port))
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
