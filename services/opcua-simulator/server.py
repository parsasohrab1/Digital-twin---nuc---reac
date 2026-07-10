"""OPC UA Server Simulator for development (HW-RQ-01, NDT-ST-03)."""

import asyncio
import math
import os
import time

from asyncua import Server, ua


async def main():
    port = int(os.getenv("OPCUA_PORT", "4840"))
    sample_rate = float(os.getenv("SAMPLE_RATE_HZ", "10"))

    server = Server()
    await server.init()
    server.set_endpoint(f"opc.tcp://0.0.0.0:{port}/freeopcua/server/")
    server.set_server_name("NDT Reactor Simulator")

    uri = "http://ndt.reactor.simulator"
    idx = await server.register_namespace(uri)

    reactor = await server.nodes.objects.add_object(idx, "Reactor")

    async def add_var(parent, name, initial):
        return await parent.add_variable(idx, name, initial)

    pressure = await reactor.add_object(idx, "Pressure")
    temp = await reactor.add_object(idx, "Temp")
    flow = await reactor.add_object(idx, "Flow")
    power = await reactor.add_object(idx, "Power")
    safety = await reactor.add_object(idx, "Safety")

    vars_map = {
        "pressure_inlet": await add_var(pressure, "Inlet", 15.5),
        "pressure_outlet": await add_var(pressure, "Outlet", 15.3),
        "temp_inlet": await add_var(temp, "Inlet", 290.0),
        "temp_outlet": await add_var(temp, "Outlet", 330.0),
        "mass_flow": await add_var(flow, "Mass", 18500.0),
        "power_thermal": await add_var(power, "Thermal", 3000.0),
        "dnbr": await add_var(safety, "DNBR", 1.8),
    }

    for node in vars_map.values():
        await node.set_writable()

    async with server:
        print(f"OPC UA Simulator running on port {port}")
        t0 = time.time()
        while True:
            t = time.time() - t0
            await vars_map["pressure_inlet"].write_value(15.5 + 0.05 * math.sin(t / 60))
            await vars_map["pressure_outlet"].write_value(15.3 + 0.04 * math.sin(t / 60))
            await vars_map["temp_inlet"].write_value(290 + 2 * math.sin(t / 3600))
            await vars_map["temp_outlet"].write_value(330 + 3 * math.sin(t / 1800))
            await vars_map["mass_flow"].write_value(18500 + 200 * math.sin(t / 120))
            await vars_map["power_thermal"].write_value(3000 * (1 + 0.02 * math.sin(t / 3600)))
            await vars_map["dnbr"].write_value(max(1.2, 1.8 - 0.001 * t / 3600))
            await asyncio.sleep(1.0 / sample_rate)


if __name__ == "__main__":
    asyncio.run(main())
