#!/usr/bin/env python3

import cv2
import numpy as np
import rospy
import os

from duckietown.dtros import DTROS, NodeType, TopicType
from duckietown_msgs.msg import Twist2DStamped, EpisodeStart
from cv_bridge import CvBridge
from sensor_msgs.msg import CompressedImage

from nn_model.constants import IMAGE_SIZE
from nn_model.model import Wrapper

from integration_activity import \
    NUMBER_FRAMES_SKIPPED, \
    filter_by_classes, \
    filter_by_bboxes, \
    filter_by_scores


class ObjectDetectionNode(DTROS):
    def __init__(self, node_name):

        # Initialize the DTROS parent class
        super(ObjectDetectionNode, self).__init__(node_name=node_name, node_type=NodeType.PERCEPTION)
        self.initialized = False
        self.log("Initializing!")

        self.veh = os.environ['VEHICLE_NAME']
        self.avoid_duckies = False

        # Construct publishers
        car_cmd_topic = f"/{self.veh}/joy_mapper_node/car_cmd"
        self.pub_car_cmd = rospy.Publisher(
            car_cmd_topic,
            Twist2DStamped,
            queue_size=1,
            dt_topic_type=TopicType.CONTROL
        )

        episode_start_topic = f"/{self.veh}/episode_start"
        rospy.Subscriber(
            episode_start_topic,
            EpisodeStart,
            self.cb_episode_start,
            queue_size=1
        )

        self.pub_detections_image = rospy.Publisher(
            "~image/compressed",
            CompressedImage,
            queue_size=1,
            dt_topic_type=TopicType.DEBUG
        )

        # Construct subscribers
        self.sub_image = rospy.Subscriber(
            f"/{self.veh}/camera_node/image/compressed",
            CompressedImage,
            self.image_cb,
            buff_size=10000000,
            queue_size=1,
        )

        self.bridge = CvBridge()

        self.v = 0.04
        aido_eval = False
        self._debug = False
        self.model_wrapper = Wrapper(aido_eval)
        self.frame_id = 0
        self.first_image_received = False
        self.initialized = True

    def cb_episode_start(self, msg: EpisodeStart):
        self.avoid_duckies = False
        self.pub_car_commands(True, msg.header)

    def image_cb(self, image_msg):
        if not self.initialized:
            self.pub_car_commands(True, image_msg.header)
            return

        self.frame_id += 1
        self.frame_id = self.frame_id % (1 + NUMBER_FRAMES_SKIPPED())
        if self.frame_id != 0:
            self.pub_car_commands(self.avoid_duckies, image_msg.header)
            return

        # Decode from compressed image with OpenCV
        try:
            bgr = self.bridge.compressed_imgmsg_to_cv2(image_msg)
        except ValueError as e:
            self.logerr("Could not decode image: %s" % e)
            return

        rgb = bgr[..., ::-1]

        rgb = cv2.resize(rgb, (IMAGE_SIZE, IMAGE_SIZE))
        bboxes, classes, scores = self.model_wrapper.predict(rgb)

        detection = self.det2bool(bboxes, classes, scores)

        # as soon as we get one detection we will stop forever
        if detection:
            self.log("Duckie pedestrian detected... stopping")
            self.avoid_duckies = True

        self.pub_car_commands(self.avoid_duckies, image_msg.header)

        if self._debug:
            colors = {0: (0, 255, 255), 1: (0, 165, 255), 2: (0, 250, 0), 3: (0, 0, 255)}
            names = {0: "duckie", 1: "cone", 2: "truck", 3: "bus"}
            font = cv2.FONT_HERSHEY_SIMPLEX
            for clas, box in zip(classes, bboxes):
                pt1 = np.array([int(box[0]), int(box[1])])
                pt2 = np.array([int(box[2]), int(box[3])])
                pt1 = tuple(pt1)
                pt2 = tuple(pt2)
                color = tuple(reversed(colors[clas]))
                name = names[clas]
                # draw bounding box
                rgb = cv2.rectangle(rgb, pt1, pt2, color, 2)
                # label location
                text_location = (pt1[0], min(pt2[1] + 30, IMAGE_SIZE))
                # draw label underneath the bounding box
                rgb = cv2.putText(rgb, name, text_location, font, 1, color, thickness=2)

            bgr = rgb[..., ::-1]
            obj_det_img = self.bridge.cv2_to_compressed_imgmsg(bgr)
            self.pub_detections_image.publish(obj_det_img)

    def det2bool(self, bboxes, classes, scores):
        box_ids = np.array(list(map(filter_by_bboxes, bboxes))).nonzero()[0]
        cla_ids = np.array(list(map(filter_by_classes, classes))).nonzero()[0]
        sco_ids = np.array(list(map(filter_by_scores, scores))).nonzero()[0]

        box_cla_ids = set(list(box_ids)).intersection(set(list(cla_ids)))
        box_cla_sco_ids = set(list(sco_ids)).intersection(set(list(box_cla_ids)))

        if len(box_cla_sco_ids) > 0:
            return True
        else:
            return False

    def pub_car_commands(self, stop, header):
        car_control_msg = Twist2DStamped()
        car_control_msg.header = header
        if stop:
            car_control_msg.v = 0.0
        else:
            car_control_msg.v = self.v

        # always drive straight
        car_control_msg.omega = 0.0

        self.pub_car_cmd.publish(car_control_msg)

    def on_shutdown(self):
        stop = Twist2DStamped(v=0.0, omega=0.0)
        self.pub_car_cmd.publish(stop)

if __name__ == "__main__":
    # Initialize the node
    object_detection_node = ObjectDetectionNode(node_name="object_detection_node")
    # Keep it spinning
    rospy.spin()
