import imp
from tkinter import *
import tkinter.messagebox
from PIL import Image, ImageTk
import socket, threading, sys, traceback, os
from RtpPacket import RtpPacket
import time 


CACHE_FILE_NAME = "cache-"

CACHE_FILE_EXT = ".jpg"



class Client:

	INIT = 0
	READY = 1
	PLAYING = 2
	state = INIT

	SETUP = 0
	PLAY = 1
	PAUSE = 2
	TEARDOWN = 3

	# Initiation..

	def __init__(self, master, serveraddr, serverport, rtpport, filename):
		self.master = master
		self.master.protocol("WM_DELETE_WINDOW", self.handler)
		self.createWidgets()
		self.serverAddr = serveraddr
		self.serverPort = int(serverport)
		self.rtpPort = int(rtpport)
		self.fileName = filename
		self.rtspSeq = 0
		self.sessionId = 0
		self.requestSent = -1
		self.teardownAcked = 0
		self.connectToServer()
		self.frameNbr = 0
		#new variables for stat calculation
		self.statExpRtpNb = 0 
		self.statCumLost = 0
		self.lastHighSeqNb = 0  
		self.statTotalPlayTime = 0 
		self.statTotalBytes = 0 
		self.statDataRate = 0
		self.statFractionLost = 0
		self.lastCumLost = 0

		
	def createWidgets(self):
		"""Build GUI."""

		# Create Setup button
		self.setup = Button(self.master, width=20, padx=3, pady=3)
		self.setup["text"] = "Setup"
		self.setup["command"] = self.setupMovie
		self.setup.grid(row=1, column=0, padx=2, pady=2)

		# Create Play button		
		self.start = Button(self.master, width=20, padx=3, pady=3)
		self.start["text"] = "Play"
		self.start["command"] = self.playMovie
		self.start.grid(row=1, column=1, padx=2, pady=2)

		# Create Pause button			
		self.pause = Button(self.master, width=20, padx=3, pady=3)
		self.pause["text"] = "Pause"
		self.pause["command"] = self.pauseMovie
		self.pause.grid(row=1, column=2, padx=2, pady=2)

		# Create Teardown button
		self.teardown = Button(self.master, width=20, padx=3, pady=3)
		self.teardown["text"] = "Teardown"
		self.teardown["command"] =  self.exitClient
		self.teardown.grid(row=1, column=3, padx=2, pady=2)
		
		# Create a label to display the movie
		self.label = Label(self.master, height=19)
		self.label.grid(row=0, column=0, columnspan=4, sticky=W+E+N+S, padx=5, pady=5) 
	
		#make the labels for streaming data
		self.stat1 = Label(self.master, text="Total Bytes Received: 0")
		self.stat1.grid(row=2, column=1, columnspan=2)
		self.stat2 = Label(self.master, text="Packets Lost: 0")
		self.stat2.grid(row=3, column=1, columnspan=2)
		self.stat3 = Label(self.master, text="Data Rate (bytes/sec): 0")
		self.stat3.grid(row=4, column=1, columnspan=2)

	def setupMovie(self):
		"""Setup button handler."""
		if self.state == self.INIT:
			self.sendRtspRequest(self.SETUP)
	

	def exitClient(self):
		"""Teardown button handler."""
		self.sendRtspRequest(self.TEARDOWN)		
		self.master.destroy() # Close the gui window
		os.remove(CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT) # Delete the cache image from video


	def pauseMovie(self):
		"""Pause button handler."""
		if self.state == self.PLAYING:
			self.statDataRate = 0 
			self.stat3.configure(text = ("Data Rate: " + str(int(self.statDataRate)) + " bytes/s"))
			self.sendRtspRequest(self.PAUSE)


	def playMovie(self):
		"""Play button handler."""
		if self.state == self.READY:
			self.statStartTime = round(time.time()*1000)
			# Create a new thread to listen for RTP packets
			threading.Thread(target=self.listenRtp).start()
			self.playEvent = threading.Event()
			self.playEvent.clear()
			self.sendRtspRequest(self.PLAY)

	
	def listenRtp(self):		
		"""Listen for RTP packets."""
		while True:
			try:
				data = self.rtpSocket.recv(20480)
				if data:
					rtpPacket = RtpPacket()
					rtpPacket.decode(data)
	
					#print("Got RTP packet with SeqNum # " + seqNb + " TimeStamp " + rtpPacket.timestamp() + " ms, of type "+ rtpPacket.payloadType());
					#seqNb = currFrameNbr
					
					curTime = round(time.time()*1000)
					self.statTotalPlayTime += curTime - self.statStartTime; 
					self.statStartTime = curTime

					currFrameNbr = rtpPacket.seqNum()
					payload = rtpPacket.getPayload()
					payload_length = len(payload)
					print ("Current Seq Num: " + str(currFrameNbr))

					self.statExpRtpNb = self.statExpRtpNb + 1  
					if currFrameNbr > self.frameNbr: # Discard the late packet
						self.frameNbr = currFrameNbr

						#calculations and update the stats
						try:
							self.statDataRate = (0) if (self.statTotalPlayTime == 0) else  (self.statTotalBytes / (self.statTotalPlayTime / 1000.0))
							self.statFractionLost = float(self.statCumLost) / self.frameNbr
							self.statTotalBytes += payload_length
							self.stat1.configure( text = ("Total Bytes Received: " + str(self.statTotalBytes)) )
							self.stat2.configure(text = ("Packet Lost Rate: " + str(self.statFractionLost)) )
							self.stat3.configure(text = ("Data Rate: " + str(int(self.statDataRate)) + " bytes/s"))
						except Exception as inst:
							print(type(inst))    # the exception instance
							print(inst.args)     # arguments stored in .args
							print(inst)     
							print("Can't update the stats")

						self.updateMovie(self.writeFrame(payload))

					if self.statExpRtpNb != currFrameNbr:
						self.statCumLost = self.statCumLost + 1  

			except:
				# Stop listening upon requesting PAUSE or TEARDOWN
				if self.playEvent.isSet(): 
					break

				# Upon receiving ACK for TEARDOWN request,

				# close the RTP socket
				if self.teardownAcked == 1:
					self.rtpSocket.shutdown(socket.SHUT_RDWR)
					self.rtpSocket.close()
					break


	def writeFrame(self, data):
		"""Write the received frame to a temp image file. Return the image file."""
		cachename = CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT
		file = open(cachename, "wb")
		file.write(data)
		file.close()

		return cachename

	

	def updateMovie(self, imageFile):
		"""Update the image file as video frame in the GUI."""
		photo = ImageTk.PhotoImage(Image.open(imageFile))
		self.label.configure(image = photo, height=288) 
		self.label.image = photo
		

	def connectToServer(self):
		"""Connect to the Server. Start a new RTSP/TCP session."""
		self.rtspSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		try:
			self.rtspSocket.connect((self.serverAddr, self.serverPort))
		except:
			tkinter.messagebox.showwarning('Connection Failed', 'Connection to \'%s\' failed.' %self.serverAddr)


	def sendRtspRequest(self, requestCode):
		"""Send RTSP request to the server."""
		# Setup request
		if requestCode == self.SETUP and self.state == self.INIT:
			threading.Thread(target=self.recvRtspReply).start()

			# Update RTSP sequence number.
			self.rtspSeq += 1

			# Write the RTSP request to be sent.
			request = 'SETUP ' + self.fileName + ' RTSP/1.0\nCSeq: ' + str(self.rtspSeq) + '\nTransport: RTP/UDP; client_port= ' + str(self.rtpPort)

			# Keep track of the sent request.
			self.requestSent = self.SETUP 

		# Play request
		elif requestCode == self.PLAY and self.state == self.READY:
			self.rtspSeq += 1
			request = 'PLAY ' + self.fileName + ' RTSP/1.0\nCSeq: ' + str(self.rtspSeq) + '\nSession: ' + str(self.sessionId)
			self.requestSent = self.PLAY

		# Pause request
		elif requestCode == self.PAUSE and self.state == self.PLAYING:
			self.rtspSeq += 1
			request = 'PAUSE ' + self.fileName + ' RTSP/1.0\nCSeq: ' + str(self.rtspSeq) + '\nSession: ' + str(self.sessionId)
			self.requestSent = self.PAUSE
			
		# Teardown request
		elif requestCode == self.TEARDOWN and not self.state == self.INIT:
			self.rtspSeq += 1
			request = 'TEARDOWN ' + self.fileName + ' RTSP/1.0\nCSeq: ' + str(self.rtspSeq) + '\nSession: ' + str(self.sessionId) 
			self.requestSent = self.TEARDOWN

		else:
			return

		# Send the RTSP request using rtspSocket.
		self.numPktsExpected = self.frameNbr - self.lastHighSeqNb
		self.numPktsLost = self.statCumLost - self.lastCumLost
		self.lastHighSeqNb = self.frameNbr
		self.lastCumLost = self.statCumLost

		self.rtspSocket.send(request.encode())
		print ('\nData sent:\n' + request)


	def recvRtspReply(self):
		"""Receive RTSP reply from the server."""
		while True:
			reply = self.rtspSocket.recv(1024)

			if reply: 
				self.parseRtspReply(reply.decode())

			# Close the RTSP socket upon requesting Teardown
			if self.requestSent == self.TEARDOWN:
				self.rtspSocket.shutdown(socket.SHUT_RDWR)
				self.rtspSocket.close()
				break


	def parseRtspReply(self, data):
		"""Parse the RTSP reply from the server."""
		lines = data.split('\n')
		seqNum = int(lines[1].split(' ')[1])
		
		# Process only if the server reply's sequence number is the same as the request's
		if seqNum == self.rtspSeq:
			session = int(lines[2].split(' ')[1])

			# New RTSP session ID
			if self.sessionId == 0:
				self.sessionId = session

			# Process only if the session ID is the same
			if self.sessionId == session:
				if int(lines[0].split(' ')[1]) == 200: 
					if self.requestSent == self.SETUP:
						# Update RTSP state.
						self.state = self.READY

						# Open RTP port.
						self.openRtpPort()

					elif self.requestSent == self.PLAY:
						self.state = self.PLAYING

					elif self.requestSent == self.PAUSE:
						self.state = self.READY

						# The play thread exits. A new thread is created on resume.
						self.playEvent.set()

					elif self.requestSent == self.TEARDOWN:
						self.state = self.INIT

						# Flag the teardownAcked to close the socket.
						self.teardownAcked = 1 


	def openRtpPort(self):
		"""Open RTP socket binded to a specified port."""

		# Create a new datagram socket to receive RTP packets from the server
		self.rtpSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

		# Set the timeout value of the socket to 0.5sec
		self.rtpSocket.settimeout(0.5)

		try:
			# Bind the socket to the address using the RTP port given by the client user
			self.rtpSocket.bind(("", self.rtpPort))

		except:
			tkinter.messagebox.showwarning('Unable to Bind', 'Unable to bind PORT=%d' %self.rtpPort)


	def handler(self):
		"""Handler on explicitly closing the GUI window."""
		self.pauseMovie()

		if tkinter.messagebox.askokcancel("Quit?", "Are you sure you want to quit?"):
			self.exitClient()
		else: 
			# When the user presses cancel, resume playing.
			self.playMovie()

