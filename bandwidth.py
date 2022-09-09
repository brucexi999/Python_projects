import ffmpeg 
import numpy as np
import subprocess 
import re
from CameraController.device.camera import Camera
import time 
import cairo
import math 
import sys
import argparse 
from statistics import mean

__authors__ = ["Bruce (Shidi) Xi", "Sunny Leung"]

# The Datapath class consists methods to produce and evaluate a scene. An engineer can manually invoke methods from this class to make qualified scenes. 
# A Controller class which will be defined later can mimic the behaviours of a human engineer to produce qualified scenes automatically, using the methods defined in Datapath. 
# Arguments:
# filepath - the directory where the script is run. 
# username, password, ip, rtsp: camera info from ACC.
# tolerance - acceptable deviation from detail targets. If high detail target is 60, tolerance is 2, then scene with high score 58-62 will be determined as meeting the target. 
class Datapath:
    def __init__ (self, filepath, username, password, ip, rtsp, tolerance):
        self.rtsp = rtsp 
        self.hor_res = 3840
        self.ver_res = 2160 # Vertical and horizontal resolution of the display device. 
        self.filepath = filepath 
        self.fineness = 9
        self.tolerance = tolerance  
        self.username = username
        self.password = password
        self.ip = ip 
        self.camera = Camera(username=self.username, password=self.password, hostip=self.ip)
        self.camera.avigilon_client.execute_console_cmd('dev')
        self.camera.avigilon_client.execute_console_cmd('sys.motiondetectionalgo') # Create the camera object in "daemonapp".
         
    # Unused argparse method, put here for potential future use. 
    def arg(self):
        parser = argparse.ArgumentParser()
        parser.add_argument("-ip", "--ip", type = str, help = "Please provide the camera IP address.", required = True)
        parser.add_argument("-un", "--username", type=str, help = "Please provide the credential username of the camera. E.g., administrator", required = True)
        parser.add_argument("-pw", "--password", nargs = "?", type = str, help = "(Optional) Please provide the credential password of the camera, if there is one. E.g., dvt")
        parser.add_argument("-hr", "--hor_res", type=int, help = "Please provide the horizontal resolution of the display. E.g., 1920", required = True)
        parser.add_argument("-vr", "--ver_res", type=int, help = "Please provide the vertical resolution of the display. E.g., 1200", required = True)
        self.args = parser.parse_args()

    # This method captures a frame from the camera's streaming using the rtsp provided.
    # It then save the captured frame into the provided filepath and name it "test.png". 
    def capture_frames (self):  
        cmd = 'ffmpeg -rtsp_transport tcp -loglevel error -i {} -y {}\\test.png'.format(self.rtsp, self.filepath)
        subprocess.run(cmd)
 
    # This method run the "detail.exe" program to check the motion scores of "test.png",
    # The stdout is then feed back here to store the high and low detail scores.
    # "detail.exe" must be at the same directory as the script is run as well as "test.png" is stored. 
    # Or, you can modify the cmd below to include the absolute paths. 
    def check_detail (self): # This method run the detail.exe program by calling it in the terminal, and get the high detail score and the low detail score. 
        cmd = 'detail "test.png"'
        try:
            output = subprocess.Popen( cmd, stdout=subprocess.PIPE ).communicate()[0] # Run the detail.exe program and feed its terminal output back to python as a byte string.
            output_str = output.decode("utf-8") # Change the bytes into string. 
            m_high = re.search(r'HighDetail:[\s](\d+)%', output_str)
            if m_high:
                self.high_detail = int(m_high.group(1))
            m_low = re.search(r'LowDetail:[\s](\d+)%', output_str)
            if m_low:
                self.low_detail = int(m_low.group(1)) # Extract high detail score and low detail score from output_str. 
        except FileNotFoundError:
            print("You are not running this script at the same directory as detail.exe")
            sys.exit()

    # This method call "ms" in daemonapp to check for the motion score. 
    def check_motion (self):
        self.sample_range = 10 # We will collect this amount of data points for the motion score. 
        self.motion_list = []
        while True: 
            daemon_output = self.camera.avigilon_client.execute_console_cmd('ms')['Output'] 
            motion_detec = int(re.search (r'30s:\s(\d+)\spercent', daemon_output).group(1)) # Call 'ms' in daemonapp, get the motion detection score. 
            self.motion_list.append(motion_detec) # Add the score into a list everytime we call 'ms'. 
            print("motion detected: ", self.motion_list[-1])
            time.sleep (3)
            if (len(self.motion_list) < self.sample_range):
                continue
            elif (len(self.motion_list) == 20): # Sometimes the score never settles, it fluctuates between two numbers. If this happens, we terminate the collection after 20 steps. 
                self.motion_score = int(mean(self.motion_list))
                break
            else: # Wait until we have engough data >= sample_range
                data_set = np.array(self.motion_list[-self.sample_range:]) 
                if (np.var(data_set) == 0): # Calculate the variance of the last N data points in the list, where N == sample_range. If variance == 0, i.e., all N data points are equal, then we can say the motion score has stabilized, the score is valid.
                    self.motion_score = data_set[-1]  # Get the latest 'ms' reading as the valid motion score. 
                    break # Once we have a valid score, we break the infinite loop. Othewise, keep calling 'ms' in daemonapp to get more motion scores.  
                else:
                    continue

    # This method makes the scenes. 
    # In a scene, there will be a motion object (rotating rectangle) that furfill the motion target.
    # There will be a background consists of a fine checkerboard pattern， which gives rise to the high detail score.
    # There will ve 1 or 2 rectangles on the right/left side of the motion object, giving rise to the low detail score. 
    # Arguments needed: 
    # motion object size (length of the rectangle)
    # length and height of the scene, same as the display device's horizontal and vertical resolutions.
    # fineness of the background bigger fineness means the background is less fine, high detail score decreases and vice versa. 
    # The position (x and y coords) and the dimensions of the rectangles, stored in dictionaries. 
    # The name of the made scene. Default is "scene". 
    def make_scene (self, mo_size = 600, length = None, height = None, fineness = 6, rect1 = {'x':0, 'y':0, 'length':0, 'height':0}, rect2 = {'x':0, 'y':0, 'length':0, 'height':0}, name = 'scene'):
        self.mo_size_y = int(self.hor_res*0.1) # The width of the rotating rectangle.
        color_grey = {'r':0.5, 'g':0.5, 'b':0.5}
        color_black = {'r':0, 'g':0, 'b':0}

        if length is None:
            length = self.hor_res
        if height is None:
            height = self.ver_res

        def make_frame (theta, label): # Output one frame in png, with the motion object rotated theta degrees around its center. 
            def drawPattern(ctx): # Draw the background square pattern. 
                ctx.move_to(0.0, 0.0)
                ctx.line_to(0.0, 1.0)
                ctx.line_to(1.0, 1.0)
                ctx.line_to(1.0, 0.0)
                ctx.line_to(0.0, 0.0)
                ctx.set_source_rgb(1.0, 1.0, 1.0)
                ctx.fill()
                ctx.rectangle(0, 0, 0.75, 0.75)
                ctx.set_source_rgb(0, 0, 0)
                ctx.fill()

            def draw_rect (rect_info, color): # Draw the low detail rectangle. 
                context.rectangle (rect_info['x'], rect_info['y'], rect_info['length'], rect_info['height'])
                context.set_source_rgb(color['r'], color['g'], color['b'])
                context.fill()

            mo_ctr_x = length/2 # Center of the rotating motion object. 
            mo_ctr_y = height/2
            mo_x = mo_ctr_x - mo_size/2 # Position of the motion object. 
            mo_y = mo_ctr_y - self.mo_size_y/2
            theta = theta*math.pi/180 # Angle of rotation in radians 

            surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, length, height)
            context = cairo.Context(surf)

            # Control how fine (high detail) the background is. 
            patternSurf = cairo.ImageSurface(cairo.FORMAT_ARGB32, fineness, fineness)
            patternCtx = cairo.Context(patternSurf)
            patternCtx.scale(fineness, fineness)
            drawPattern(patternCtx)
            patternSurf.flush()

            context.move_to(0.0, 0.0)
            context.line_to(0.0, height)
            context.line_to(length, height)
            context.line_to(length, 0.0)
            context.line_to(0.0, 0.0)
            context.set_source_surface(patternSurf)
            context.get_source().set_extend(cairo.Extend.REPEAT) # Fill the background with repeated square pattern. 
            context.fill()

            draw_rect (rect1, color_grey)
            draw_rect (rect2, color_grey) # Draw two rectangles as a control of the low level detail.
 
            context.translate(mo_ctr_x, mo_ctr_y) 
            context.rotate(theta)
            context.translate(-mo_ctr_x, -mo_ctr_y)
            mo_info = {'x':mo_x, 'y':mo_y, 'length':mo_size, 'height':self.mo_size_y}
            draw_rect (mo_info, color_black) # Draw the motion object, with the center of rotation being its own center.

            surf.write_to_png(self.filepath+"\\bw_frames\\"+label+".png")

        # The rotating rectangle spins for 180 degrees to overlap with itself. 
        for i in range (0, 181, 9): # Maybe add a new feature here to control the frame rate? Currantly, each frame the rectangle rotates for 9 degrees. Increasing this number will increase the speed of rotation.
            make_frame(theta = i, label = str(int(i/9))) # Generate frames 
        (
            ffmpeg # Use ffmpeg to combine frames into a video 
            .input(self.filepath+"\\bw_frames\\%d.png", framerate=50 )
            .output(self.filepath+"\\bw_frames\\video.mp4", pix_fmt = "yuv420p", loglevel = "fatal")
            .run(overwrite_output=True)
        )
        cmd = 'ffmpeg -t 03:00 -loglevel fatal -y -stream_loop 1000 -i "{}\\bw_frames\\video.mp4" -c copy "{}\\bw_frames\\{}.mp4"'.format(self.filepath, self.filepath, name)
        subprocess.run(cmd) # Use ffmpeg to extend the video to 3 min.    

    def play_scene (self):
        cmd = 'ffplay -fs -loglevel fatal -autoexit "{}\\bw_frames\\scene.mp4"'.format(self.filepath)
        subprocess.Popen(cmd)

    # A wrapper method of play_scene, sleep for a provided period of time, capture a frame and check for detail/motion scores.
    def pscc (self, wait_time, parameter):  
        self.play_scene()
        time.sleep(wait_time)
        if (parameter == 'detail'):
            self.capture_frames()
            self.check_detail()
        elif (parameter == 'motion'):
            self.check_motion()
        else:
            print("Please specify which parameter you want to check, detail or motion?")
            sys.exit()

# The Controller class inherits the Datapath class such that it can invoke the methods defined in Datapath. 
# It automatically make the scenes. 
class Controller (Datapath):
    # Use bisection to get a value of self.fineness that produces a motion object meeting the motion target. 
    def make_motion (self):
        print("Making the motion object")
        x1 = 0
        x2 = self.hor_res
        while True:
            self.mo_size = (x1+x2)/2 
            print("New motion object size: ", self.mo_size)
            self.make_scene(mo_size=self.mo_size)
            self.pscc(30.0, 'motion')
            print("motion score: ", self.motion_score)

            if (self.motion_score == self.motion_target):
                print("motion done! ")
                break # Completed.
            elif (self.motion_score > self.motion_target):
                x2 = self.mo_size 
            elif (self.motion_score < self.motion_target):
                x1 = self.mo_size
            
    # A method to get the desired high detail score by controlling self.fineness. It produces a self.fineness value which gives a high detail score closest to our target. 
    def hd_delta (self): # Full name: high detail delta
        delta_i = abs(self.high_detail-self.high_detail_target) # Inital difference between the high detail score and the target
        print("initil delta: ", delta_i)
        while True: 
            self.fineness += 1
            print("Making new scene with increased fineness.")
            self.make_scene(mo_size=self.mo_size, fineness=self.fineness, rect1= self.rect1, rect2= self.rect2)
            self.pscc(5.0, 'detail')
            delta = abs(self.high_detail-self.high_detail_target) # We increase the fineness by 1, then check the high score again, calculate the new difference. 
            print("hs: ", self.high_detail)
            if (delta > delta_i): # The difference either gets bigger or smaller. if it gets bigger, then we are moving away from the optimum fineness as we increase it. We should instead decrease the fineness. 
                print("We are moving away.")
                self.direction = -1
                break
            elif (delta < delta_i): # If the difference gets smaller, we are moving towards the optimum, we should simply keep increasing fineness. 
                print("We are moving towards.")
                self.direction = 1
                break
            else:
                print("new delta equals initial delta, repeat.")
                continue 

        while True:
            self.fineness += self.direction
            print("new fineness: ", self.fineness)
            previous_delta = delta
            self.make_scene(mo_size=self.mo_size, fineness=self.fineness, rect1= self.rect1, rect2= self.rect2)
            self.pscc(5.0, 'detail')
            delta = abs(self.high_detail-self.high_detail_target) # Make a new scene with the new fineness, compare the delta with the old delta. 
            print("new delta: {}, new hs: {}".format (delta, self.high_detail))
            if (delta < previous_delta):
                continue
            elif (delta > previous_delta): # Delta should keep decreasing, i.e, we should be keep moving towards the optimum fineness until we observe the first raise. That means we just passed the optimal. 
                self.fineness += -self.direction # Go one step backwards to get the optimal. 
                print("optimum fineness: ", self.fineness)
                break
    
    # A method that uses bisection to produces self.rect1 and self.rect2 that will meet the low detail target. 
    def ld_rect (self): 
        # Tune rect1 or rect2, then pscc. 
        def tune_rect (rect_num): # rect_num is 1 or 2, suggesting whether we are tunning rect1 or 2. 
            rect_num += -1 # Minus 1 to be a list index. 
            self.rects[rect_num] = {'x':self.poss[rect_num], 'y':0, 'length':self.lengths[rect_num], 'height':self.height}
            self.rect1 = self.rects[0]
            self.rect2 = self.rects[1]
            self.make_scene(mo_size=self.mo_size, fineness=self.fineness, rect1=self.rect1, rect2=self.rect2) # If rect_num = 1 self.rect2 = {'x':0, 'y':0, 'length':0, 'height':0}
            self.pscc(5.0, 'detail')
            print("Length1: {}, length2: {}, low score: {}".format(self.lengths[0], self.lengths[1], self.low_detail))

        # The bisection algorithm, keep tuning rect1 or rect2 using the algorithm until low detail target is met. 
        def bisection (rect_num):
            rect_num += -1
            x1 = self.poss[rect_num]
            x2 = self.x_limit + rect_num * (self.hor_res - self.x_limit) # if rect_num = 0 (we are bisectioning rect1) then x2 = self.x_limit, if rect_num = 1 (we are bisectioning rect2) then x2 = self.hor_res. 
            print("Initial x1: {}, initial x2: {}".format(x1, x2))
            while True:
                self.lengths[rect_num] = (x2+x1)/2 - self.poss[rect_num]
                print("New length {}: {}".format(rect_num+1, self.lengths[rect_num]))
                tune_rect (rect_num+1)
                if (self.low_detail < self.low_detail_target - self.tolerance):
                    x1 = self.lengths[rect_num] + self.poss[rect_num]
                    print("Landed on the left, new x1: {}, new x2: {}".format(x1, x2))
                elif (self.low_detail > self.low_detail_target + self.tolerance):
                    x2 = self.lengths[rect_num] + self.poss[rect_num]
                    print("Landed on the right, new x1: {}, new x2: {}".format(x1, x2))
                elif (abs(self.low_detail - self.low_detail_target) <= self.tolerance):
                    print ('Low detail score furfilled.')
                    break

        self.height = self.ver_res # Rect1 or rect2 width, equals to the vertical resolution. 
        self.box_motion_margin = 100 # The distance between rectangles and the motion object. The two cannot overlap. 
        self.x_limit = self.hor_res/2 - int(math.sqrt((self.mo_size/2)**2 + (self.mo_size_y/2)**2)) - self.box_motion_margin # Maximum length rect1 or rect2 can have. 
        self.length_1 = self.x_limit 
        self.length_2 = 0 # Initial value of rect1 and rect2 lengths, will be changed later. 
        self.rect1_x_pos = 0 # x coords of rect1's position. It starts at the very left of the screen, 
        self.rect2_x_pos = self.hor_res - self.x_limit # Rect2 start2 at the side of the motion object. 
        self.rect1 = {'x':self.rect1_x_pos, 'y':0, 'length':self.length_1, 'height':self.height}
        self.rect2 = {'x':self.rect2_x_pos, 'y':0, 'length':self.length_2, 'height':self.height} # Initial info of rect1 and rect2, will be changed later. 
        self.rects = [self.rect1, self.rect2]
        self.poss = [self.rect1_x_pos, self.rect2_x_pos]
        self.lengths = [self.length_1, self.length_2] # Lists of rect1 and rect2 info. 

        # Find the target box length by bisection. 
        print("Checking should we invoke rect2.")
        tune_rect (1)
        if (self.low_detail < self.low_detail_target - self.tolerance): 
            # We used all the availbilities of rect1, but low detail score is still smaller than the lower bound of our target, we need to invoke rect2.
            print("Bisection 2")
            bisection(2)

        elif (self.low_detail > self.low_detail_target + self.tolerance):
            # Rect1 is enough, bisection rect1. 
            print("Bisection 1")
            bisection(1)

        elif (abs(self.low_detail - self.low_detail_target) <= self.tolerance):
            # Should never appear, but included here anyway. 
            print ('Low detail score furfilled.')

    # This method makes the high detail scenes. Motion target can be 5 or 1.  
    def high_detail_scenes (self, motion_target): 
        self.high_detail_target = 60
        self.low_detail_target = 30
        self.motion_target = motion_target
        self.box_motion_margin = 100 
        self.rect1 = {'x':0, 'y':0, 'length':0, 'height':0}
        self.rect2 = {'x':0, 'y':0, 'length':0, 'height':0}
        print("Making high detail {}% motion.".format(self.motion_target))
        # We first deal with the motion score. 
        self.make_motion()
        # We then move on to deal with the high detail score. 
        self.make_scene(mo_size=self.mo_size, fineness=self.fineness)
        self.pscc(5.0, 'detail')

        self.hd_delta()
        self.make_scene(mo_size=self.mo_size, fineness=self.fineness)
        self.pscc(5.0, 'detail')
        
        if (abs(self.high_detail - self.high_detail_target) <= self.tolerance):
            print("hs meets the target")
            hs_pass = 1
        elif (self.high_detail > self.high_detail_target + self.tolerance):
            print("hs higher than target")
            hs_pass = 0
        elif (self.high_detail < self.high_detail_target - self.tolerance):
            print("hs lower than target")
            hs_pass = 0
            self.fineness += -1  # Although the current fineness is the closest to our target, but it is below it, that means we need to increase fineness by 1 to get it above the target, and tune it down with boxes. 
            self.make_scene(mo_size=self.mo_size, fineness=self.fineness)
            self.pscc(5.0, 'detail')

        # Use low detail boxes to tune the high detail score.
        self.rect1_x_pos = int (self.hor_res*0.1)
        self.x_limit = self.hor_res/2 - int(math.sqrt((self.mo_size/2)**2 + (self.mo_size_y/2)**2)) - self.box_motion_margin - self.rect1_x_pos
        self.height = self.ver_res
        self.length_1 = 0
        
        if (not hs_pass):
            x1 = self.rect1_x_pos
            x2 = self.x_limit  
            print("Initial x1: {}, initial x2: {}".format(x1, x2))
            while True:
                self.length_1 = (x2+x1)/2 - self.rect1_x_pos
                print("New length1: {}".format(self.length_1))
                self.rect1 = {'x':self.rect1_x_pos, 'y':0, 'length':self.length_1, 'height':self.height}
                self.make_scene(mo_size=self.mo_size, fineness=self.fineness, rect1=self.rect1)
                self.pscc(5.0, 'detail')
                print("Length 1: {}, high score: {}, low score: {}".format(self.length_1, self.high_detail, self.low_detail))
                if (self.low_detail >= self.low_detail_target):
                    print("Cannot make high detail scene...low detail exceeds the limit, please manually make it using Datapath.")
                    break
                elif (self.high_detail < self.high_detail_target - self.tolerance): # Too much low detail, shrink the box 
                    x2 = self.length_1 + self.rect1_x_pos
                    print("Landed on the left, new x1: {}, new x2: {}".format(x1, x2))
                elif (self.high_detail > self.high_detail_target + self.tolerance):
                    x1 = self.length_1 + self.rect1_x_pos
                    print("Landed on the right, new x1: {}, new x2: {}".format(x1, x2))
                elif (abs(self.high_detail - self.high_detail_target) <= self.tolerance):
                    print ('high detail score furfilled.')
                    break   
        
        print("High detail {}% is done, motion score: {}, high detail score: {}, low detail score: {}".format(str(self.motion_target), self.motion_score, self.high_detail, self.low_detail))
        self.make_scene(mo_size=self.mo_size, fineness=self.fineness, rect1=self.rect1, name = 'high_{}%'.format(self.motion_target))
    
    # This method makes the low detail scenes. Motion target can be 5 or 1.
    def low_detail_scenes (self, motion_target): 
        self.high_detail_target = 30
        self.low_detail_target = 60
        self.motion_target = motion_target
        print("Making low detail {}% motion.".format(self.motion_target))

        # We first deal with the motion score. 
        self.make_motion()
        
        # When motion is done, control the fineness to get the high detail score below the threshold.

        self.make_scene(mo_size=self.mo_size, fineness=self.fineness)
        self.pscc(5.0, 'detail')
        while True:
            self.pscc(5.0, 'detail')
            print("hs: {}, ls: {}".format(self.high_detail, self.low_detail))
            if (self.high_detail >= self.high_detail_target):
                self.fineness += 1 
                self.make_scene(mo_size=self.mo_size, fineness=self.fineness)
            elif (self.high_detail < self.high_detail_target):
                self.fineness += 1 # Even if the current fineness gives a high detail score below the limit, we increase the fineness one step more to make sure high detail score will remain below the limit at all time. 
                self.make_scene(mo_size=self.mo_size, fineness=self.fineness)
                break
        
        # Once we make sure high detail score will remain below the limit, we draw the low detail boxes. 
        self.ld_rect()

        self.make_scene(mo_size=self.mo_size, fineness=self.fineness, rect1=self.rect1, rect2=self.rect2, name = 'low_{}%'.format(self.motion_target))

        print("Low detail {}% is done, motion score: {}, high detail score: {}, low detail score: {}".format(str(self.motion_target), self.motion_score, self.high_detail, self.low_detail))

    # This method makes the medium detail scenes. Motion target can be 5 or 1.
    def medium_detail_scenes (self, motion_target):
        self.high_detail_target = 30
        self.low_detail_target = 30
        self.motion_target = motion_target
        print("Making medium detail {}% motion.".format(self.motion_target))

        self.make_motion()

        self.make_scene(mo_size=self.mo_size, fineness=self.fineness)
        self.ld_rect()
        
        self.hd_delta()

        self.make_scene(mo_size=self.mo_size, fineness=self.fineness, rect1=self.rect1, rect2=self.rect2, name = 'medium_{}%'.format(self.motion_target))

        self.pscc(5.0, 'detail')
        print("Medium detail {}% is done, motion score: {}, high detail score: {}, low detail score: {}".format(str(self.motion_target), self.motion_score, self.high_detail, self.low_detail))

def main ():
    # Illustration of making the scenes automatically using the Controller methods. 
    '''a = Controller (username='administrator', password='', ip='10.89.114.152', rtsp='rtsp://administrator:@10.89.114.152/defaultPrimary?streamType=m', tolerance=2, filepath='D:\\Motorola Co-op\\Test-automation')
    a.high_detail_scenes(motion_target = 1)
    a.high_detail_scenes(motion_target = 5)
    a.low_detail_scenes(motion_target = 1)
    a.low_detail_scenes(motion_target = 5)
    a.medium_detail_scenes(motion_target = 1)
    a.medium_detail_scenes(motion_target = 5)'''

    # Illustration of making the scenes by manually calling methods in Datapath. 
    b = Datapath(username='administrator', password='', ip='10.89.114.152', rtsp='rtsp://administrator:@10.89.114.152/defaultPrimary?streamType=m', tolerance=2, filepath='D:\\Motorola Co-op\\Test-automation')
    b.make_scene(mo_size=1000, fineness=10, rect1={'x':0, 'y':0, 'length':500, 'height':b.ver_res}, rect2 = {'x':2500, 'y':0, 'length':500, 'height':b.ver_res}, name='high_detail_5%')
    # b.pscc(5.0, 'detail')
if __name__ == "__main__":
    main ()                     