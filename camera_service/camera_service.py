import os
import asyncio
import numpy as np
from caproto.server import ioc_arg_parser, run, pvproperty, PVGroup
import simulacrum
import zmq
import time
from zmq.asyncio import Context
import pickle

class ProfMonService(simulacrum.Service):
    default_image_size = 1024*1024

    def __init__(self):
        print('Initializing PVs') 
        super().__init__()

        #load Profmon properties from file
        with open('screenProps5.dat', 'rb') as file_handle:
            screens = pickle.load(file_handle);

        #build dicts to translate element name/device name
        self.ele2dev = {}
        self.dev2ele = {}
        self.profiles = {}
        for screenProps in screens:
                self.ele2dev[screenProps['element_name']] = screenProps['device_name']
                self.dev2ele[screenProps['device_name']] = screenProps['element_name']
                self.profiles[screenProps['device_name']] = {'props': screenProps}
 
        def ProfMonPVClassMaker(screenProps):
            pvLen = len(screenProps['device_name']);
            image_name = screenProps['image_name'][pvLen:];
            image_size =  int(screenProps['values'][0] * screenProps['values'][1])
            if not image_size:
                image_size = self.default_image_size;

            image= pvproperty(value=np.zeros(image_size).tolist(), name = image_name, read_only=True, mock_record='ai')

            try:
                pvProps = { screenProps['props'][i].split(':')[3]: pvproperty(value = float(screenProps['values'][i]), name = ':' + screenProps['props'][i].split(':')[3], read_only=True, mock_record='ai') 
                            for i in range(0, len(screenProps['props'])) if screenProps['props'][i]
                        }
            except IndexError:
                print(screen + ' has an invalid device name')
                return None;

            pvProps['image'] = image;
            return type(screenProps['device_name'], (PVGroup,), pvProps)

        screen_pvs = {};          
        for screen in self.profiles:
            print('PV: ' + screen + ' ' + self.dev2ele[screen]);
            ProfClass = ProfMonPVClassMaker(self.profiles[screen]['props'])
            if(ProfClass):
                screen_pvs[screen] = ProfClass(prefix = screen);

        self.add_pvs(screen_pvs)
        self.ctx = Context.instance()
        #cmd socket is a synchronous socket, we don't want the asyncio context.
        self.cmd_socket = zmq.Context().socket(zmq.REQ)
        self.cmd_socket.connect("tcp://127.0.0.1:{}".format(os.environ.get('MODEL_PORT', 12312)))
        
        print("Initialization complete.")

    def get_image_size(self, screenProps):
        screenX = screenProps['values'][0];
        screenY = screenProps['values'][1];
        return int(screenX * screenY);

    def request_profiles(self):
        self.cmd_socket.send_pyobj({"cmd": "send_profiles_twiss"})
        return self.cmd_socket.recv_pyobj();
        
    async def recv_profiles(self, flags=0, copy=False, track=False):
        profile_socket = self.ctx.socket(zmq.SUB)
        profile_socket.connect('tcp://127.0.0.1:{}'.format(os.environ.get('PROFILE_PORT', 12345)))
        profile_socket.setsockopt(zmq.SUBSCRIBE, b'')
        while True:
            print("Checking for new profile data.")
            md = await profile_socket.recv_pyobj(flags=flags)
            print("Profile data incoming: ", md)
            msg = await profile_socket.recv(flags=flags, copy=copy, track=track)
            buf = memoryview(msg)
            A = np.frombuffer(buf, dtype=md['dtype'])
            result = A.reshape(md['shape'])[3:-3]

            for row in result:
                ( _, name, _, _, _, beta_a, beta_b) = row.split();
                devName = self.ele2dev[name];
                if devName not in self.profiles:
                    continue
                image_size = self.get_image_size(self.profiles[devName]['props']);

                #CGI
                image = np.ones(image_size)* float(beta_a);
                self.profiles[devName]['image'] = image.tolist(); 

            await self.publish_profiles()

    async def publish_profiles(self):
        for key, profile in self.profiles.items():
            pvName = profile['props']['image_name']
            if pvName in self:
                try:
                    await self[pvName].write(profile['image'])
                    print('Publishing profile: ' + key);
                except:
                    continue
    
def main():
    service = ProfMonService()
    loop = asyncio.get_event_loop()
    _, run_options = ioc_arg_parser(
        default_prefix='',
        desc="Simulated Profile Monitor Service")
    loop.create_task(service.recv_profiles())
    loop.call_soon(service.request_profiles)
    run(service, **run_options)
    
if __name__ == '__main__':
    main()
