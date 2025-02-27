#!/usr/bin/env python3

import rospy
from enum import Enum, auto
from typing import Tuple

# Gains for heading PID control
HEADING_KP = 0.25
HEADING_KI = 0.4
HEADING_KD = 0.027
INT_MAX = 0.04      # anti-windup for the integral term
OMEGA_0 = 0.3       # default omega for scanning
OMEGA_SCALING = 1   # scaling the omega value from the PID controller
OMEGA_OFFSET = 0.1  # offset to overcome initial motor and wheel friction
V_0 = 0.02

class State(Enum):
    SCANNING = auto()        # scanning for objects
    # DETECTED_ANY = auto()  # an object is found, looking for the desired one, rotate around until desired object is identified
    IDENTIFIED = auto()      # desired object identified, drive towards it
    CAPTURED = auto()        # desired object captured within the "grabber"
    DELIVERING = auto()      # desired object is being delivered
    DELIVERED = auto()       # desired object is sucessfully delivered

class StateMachine:
    def __init__(self):
        self.state = State.SCANNING

    def transition(self, new_state: State):
        self.state = new_state


def scanning(car_control_msg, new_state, duckiedata, current_obj, omega):
    # Turning until desired object is detected
    # rospy.loginfo("SCANNING")
    car_control_msg.v = 0
    car_control_msg.omega = omega

    for i in range(round(duckiedata[0])):
    # compare ids of detected objects with desired object id:
        if duckiedata[i*3+3] == current_obj:
            new_state = State.IDENTIFIED
            car_control_msg.v = 0
            car_control_msg.omega = 0
            rospy.loginfo("desired object found")

    return car_control_msg, new_state


def approach(car_control_msg, new_state, duckiedata, current_obj, prev_e, prev_int, delta_t, no_det_count, close_count, v, delivery):

    i = 0
    e = prev_e
    e_int = prev_int
    obj_ids = []
    for i in range(round(duckiedata[0])):
        # compare ids of detected objects with desired object id:
        if duckiedata[i*3+3] == current_obj:

            obj_ids.append(current_obj)
            no_det_count = 0       # reset 'not detected' counter because we have detected the desired object
            r = duckiedata[i*3+1]
            theta = duckiedata[i*3+2]

            theta_r = 0     # desired heading: angle zero, i.e. object straight ahead
            gains = (HEADING_KP, HEADING_KI, HEADING_KD)
            # PID controller for heading
            v, omega, e, e_int, e_der = PIDController(v, theta_r, theta, prev_e, prev_int, delta_t, gains)
            
            if delivery is True:
                r_0 = 0.2
            else:
                r_0 = 0.16
            
            if r > 0.25:
                rospy.loginfo("r>0.25")
                close_count = 0
                car_control_msg.v = v   # 0.02
                car_control_msg.omega = omega*OMEGA_SCALING
            elif r <= 0.25 and r > r_0:
                rospy.loginfo("r>%.2f" % r_0)
                close_count = 0
                car_control_msg.v = 0.8*v   # 0.01
                car_control_msg.omega = omega*OMEGA_SCALING
            else:
                rospy.loginfo("r<%.2f" % r_0)
                if abs(omega*OMEGA_SCALING) < OMEGA_OFFSET + 0.065:   # increment only if control action small
                    close_count += 1
                car_control_msg.v = 0
                car_control_msg.omega = omega*OMEGA_SCALING
                if close_count >= 8:     # move to captured if close enough
                    close_count = 0
                    new_state = State.CAPTURED
    
    # desired object not found, stop. If not found for a couple of iterations, go back to scanning
    if current_obj not in obj_ids:
        no_det_count += 1
        car_control_msg.v = 0
        car_control_msg.omega = 0
        rospy.loginfo("Identified: object not found")
        if no_det_count >= 4:
            new_state = State.SCANNING
            rospy.loginfo("Identified: object not found, switch to scanning")
            no_det_count = 0
    

    # once within a certain distance, move forward a bit more, then change to captured state
    return car_control_msg, new_state, e, e_int, no_det_count, close_count


def delivered(car_control_msg, new_state):

    car_control_msg.v = 0
    car_control_msg.omega = 0

    return car_control_msg, new_state


def PIDController(v_0: float,
                  theta_ref: float,
                  theta_hat: float,
                  prev_e: float,
                  prev_int: float,
                  delta_t: float,
                  gains: Tuple[float, float, float]
                  ) -> Tuple[float, float, float, float]:
    """
    Args:
        v_0 (:double:) linear Duckiebot speed (given).
        theta_ref (:double:) reference heading pose
        theta_hat (:double:) the current estiamted theta.
        prev_e (:double:) tracking error at previous iteration.
        prev_int (:double:) previous integral error term.
        delta_t (:double:) time interval since last call.
        gains (:Tuple:) PID controller gains
    returns:
        v_0 (:double:) linear velocity of the Duckiebot 
        omega (:double:) angular velocity of the Duckiebot
        e (:double:) current tracking error (automatically becomes prev_e_y at next iteration).
        e_int (:double:) current integral error (automatically becomes prev_int_y at next iteration).
    """
    
   # Tracking error
    e = theta_ref - theta_hat

    # integral of the error
    e_int = prev_int + e*delta_t

    # anti-windup - preventing the integral error from growing too much
    e_int = max(min(e_int, INT_MAX), -INT_MAX)

    # derivative of the error
    e_der = (e - prev_e)/delta_t

    # controller coefficients
    # Kp = 5      # 15
    # Ki = 0.2    # 1
    # Kd = 0.1    # 0.2
    (Kp, Ki, Kd) = gains

    # PID controller for omega
    omega_p = Kp*e
    omega_i = Ki*e_int
    omega_d = Kd*e_der

    omega = omega_p + omega_i + omega_d

    if omega > 0:
        omega = omega + OMEGA_OFFSET
    elif omega < 0:
        omega = omega - OMEGA_OFFSET
    else:
        pass

    rospy.loginfo("PID:o%.3f,p%.3f,i%.3f,d%.3f,e%.3f", omega, omega_p, omega_i, omega_d, e)

    return v_0, omega, e, e_int, e_der