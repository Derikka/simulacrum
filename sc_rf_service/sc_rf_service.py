import numpy as np
import sys
import os
import asyncio
from collections import OrderedDict
from caproto.server import ioc_arg_parser, run, pvproperty, PVGroup
from caproto import ChannelType
import simulacrum
import zmq
from zmq.asyncio import Context

#set up python logger
#L = simulacrum.util.SimulacrumLog(os.path.splitext(os.path.basename(__file__))[0], level='INFO')

class CavityPV(PVGroup):
    pdes = pvproperty(value=0.0, name=':PDES', precision=1)  
    phas = pvproperty(value=0.0, name=':PHASE', read_only=True, precision=1)
    gdes = pvproperty(value=100.0, name=':GDES', precision=1)
    pref = pvproperty(value=0.0, name=':PREF', precision=1)
    ssa_ctrl = pvproperty(value=1, name=':SSA:PowerOn', dtype=ChannelType.ENUM,
                      enum_strings=("OFF", "ON"))
    z = pvproperty(value=0.0, name = ':Z', read_only = True, precision=1)
    def __init__(self, device_name, change_callback, initial_values, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.device_name = device_name 
        self.element_name = initial_values[3]
        self.ssa_on = True
        self.gdes_i = initial_values[0]*1.e-6
        self.pdes_i = initial_values[1] *360 #initial_values[1] *360
        self.gdes._data['value'] = initial_values[0]*1.e-6
        self.pdes._data['value'] = initial_values[1] *360
        self.phas._data['value'] = initial_values[1] * 360 
        self.z._data['value'] = initial_values[2]
        self.change_callback = change_callback 

    @pdes.putter
    async def pdes(self, instance, value):
        self.change_callback(self, value, "PDES")
        return;
    @gdes.putter
    async def gdes(self, instance, value):
        self.change_callback(self,value, "GDES")
        return
    @pref.putter
    async def pref(self, instance, value):
        self.change_callback(self, value, "PREF")
        return
    @ssa_ctrl.putter
    async def ssa_ctrl(self, instance, value):
        self.change_callback(self, value, "SSA_ON");
        return

def _parse_cav_table(table):
    splits = [row.split() for row in table]
    return { simulacrum.util.convert_element_to_device(elemName): (float(bmadGrad), float(bmadPhas), float(Z), elemName) for (_, elemName, _, Z, _, bmadGrad, bmadPhas) in splits }

def _make_linac_table(init_vals):
    L2list = ''.join([f"CAVL{number:02d}*," for number in range(4,16)])
    L3_1list = ''.join([f"CAVL{number:02d}*," for number in range(16,26)])
    L3_2list = ''.join([f"CAVL{number:02d}*," for number in range(26,36)])
    sections = {"L1B" : ("ACCL:L1B:0210", "CAVL02*,CAVL03*,"), "HL1B" : ("ACCL:L1B:H110", "CAVC01*,CAVC02*,") , "L2B" : ("ACCL:L2B:0410", L2list), "L3B1" : ("ACCL:L3B:1610", L3_1list), "L3B2" : ("ACCL:L3B:1610", L3_2list)};
    linac_pvs = {}
    for section in sections.keys():
        device = sections[section]
        device_name = device[0];
        element = device[1];
        linac_pvs["ACCL:" + section + ":ALL"] = init_vals[device_name][:3] + (element[:-1],)
    print(linac_pvs)
    return linac_pvs

class CavityService(simulacrum.Service):
    def __init__(self):
        super().__init__()
        self.ctx = Context.instance()
        #cmd socket is a synchronous socket, we don't want the asyncio context.
        self.cmd_socket = zmq.Context().socket(zmq.REQ)
        self.cmd_socket.connect("tcp://127.0.0.1:{}".format(os.environ.get('MODEL_PORT', 12312)))
        init_vals = self.get_cavity_ACTs_from_model()
        for cavity in init_vals.keys():
            print(cavity)
        cav_pvs = {device_name: CavityPV(device_name, self.on_cavity_change, initial_values=init_vals[device_name], prefix=device_name) for device_name in init_vals.keys()}
        linac_init_vals = _make_linac_table(init_vals)
        linac_pvs = {device_name: CavityPV(device_name, self.on_cavity_change, initial_values=linac_init_vals[device_name], prefix=device_name) for device_name in linac_init_vals.keys()}
        self.add_pvs(cav_pvs);
        self.add_pvs(linac_pvs);
        print("Initialization complete.")

    def get_cavity_ACTs_from_model(self):
        init_vals = {}
        self.cmd_socket.send_pyobj({"cmd": "tao", "val": "show lat -no_label_lines -attribute gradient -attribute phi0 lcavity::* -no_slaves"})
        table = self.cmd_socket.recv_pyobj()['result']
        init_vals = _parse_cav_table(table)
        return init_vals
    
    def on_cavity_change(self, cavity_pv, value, parameter):
        element = cavity_pv.element_name
        if parameter == "PREF":
            return
        elif parameter == "PDES":
            cav_attr = "phi0_err";
            value = (value - cavity_pv.pdes_i - cavity_pv.pref._data['value'])/360.0;
        elif parameter == "GDES":
            value = (value - cavity_pv.gdes_i)*1e6
            cav_attr = "gradient_err";
        elif parameter == "SSA_ON":
            cav_attr = "is_on";
            value = 'T' if value is 'ON' else 'F' 
        cmd = f'set ele {element} {cav_attr} = {value}'
        print(cmd)
        self.cmd_socket.send_pyobj({"cmd": "tao", "val": cmd})         
        self.cmd_socket.recv_pyobj()   

def main():
    service = CavityService()
    loop = asyncio.get_event_loop()
    _, run_options = ioc_arg_parser(
        default_prefix='',
        desc="Simulated CM Cavity Service")
    run(service, **run_options)
    
if __name__ == '__main__':
    main()
    



        
