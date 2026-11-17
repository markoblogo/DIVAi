// TimeBox Mini transport, adapted from den-sv/div-box at
// 2405b9d823cc0aaf08697ec6a02007cfedf2c936. See LICENSE-div-box.txt.
// One parent process owns this bridge. It reads hex-encoded frames on stdin.
import Foundation
import IOBluetooth
import Darwin

func reply(_ value: String) {
    FileHandle.standardOutput.write(Data((value + "\n").utf8))
}

func fail(_ reason: String) -> Never {
    reply("ERROR " + reason)
    exit(1)
}

func normalizedAddress(_ address: String) -> String {
    return address.replacingOccurrences(of: "-", with: ":").uppercased()
}

func targetDevice() -> IOBluetoothDevice {
    let paired = (IOBluetoothDevice.pairedDevices() ?? []).compactMap { $0 as? IOBluetoothDevice }
    if let selected = ProcessInfo.processInfo.environment["DIVOOM_TIMEBOX_ADDRESS"], !selected.isEmpty {
        guard let dev = paired.first(where: {
            normalizedAddress($0.addressString ?? "") == normalizedAddress(selected)
        }) else { fail("The specified TimeBox address is not paired with this Mac.") }
        return dev
    }
    let candidates = paired.filter {
        let name = ($0.name ?? "").lowercased().filter { $0.isLetter || $0.isNumber }
        return name.contains("timeboxmini") && !name.contains("light")
    }
    if candidates.count == 1 { return candidates[0] }
    if candidates.isEmpty {
        fail("Pair TimeBox-mini-audio in System Settings > Bluetooth, allow Bluetooth access, then retry. For a renamed speaker set DIVOOM_TIMEBOX_ADDRESS.")
    }
    fail("Several TimeBox Minis are paired. Set DIVOOM_TIMEBOX_ADDRESS to the Bluetooth address of the one to use.")
}

final class Link: NSObject, IOBluetoothRFCOMMChannelDelegate {
    var channel: IOBluetoothRFCOMMChannel?

    func start(_ device: IOBluetoothDevice) {
        var id: BluetoothRFCOMMChannelID = 4
        if let rec = device.getServiceRecord(for: IOBluetoothSDPUUID(uuid16: 0x1101)) {
            var discovered: BluetoothRFCOMMChannelID = 0
            if rec.getRFCOMMChannelID(&discovered) == kIOReturnSuccess && discovered > 0 {
                id = discovered
            }
        }
        var candidate: IOBluetoothRFCOMMChannel?
        let status = device.openRFCOMMChannelAsync(&candidate, withChannelID: id, delegate: self)
        guard status == kIOReturnSuccess else { fail("Could not open the display channel (\(status)). Close any app using the display and retry.") }
    }

    func rfcommChannelOpenComplete(_ opened: IOBluetoothRFCOMMChannel!, status: IOReturn) {
        guard status == kIOReturnSuccess, let openedChannel = opened else {
            fail("Bluetooth display connection failed (\(status)). Check power, pairing and other Divoom apps.")
        }
        channel = openedChannel
        reply("READY")
    }

    func rfcommChannelClosed(_ closed: IOBluetoothRFCOMMChannel!) {
        fail("The TimeBox Mini display channel disconnected.")
    }

    func writeFrame(_ bytes: [UInt8]) {
        guard let ch = channel, ch.isOpen() else { fail("The TimeBox Mini is disconnected.") }
        let mtu = max(1, Int(ch.getMTU()))
        var data = bytes
        var offset = 0
        while offset < data.count {
            let count = min(mtu, data.count - offset)
            let status = data.withUnsafeMutableBytes { (buffer: UnsafeMutableRawBufferPointer) in
                ch.writeSync(buffer.baseAddress!.advanced(by: offset), length: UInt16(count))
            }
            guard status == kIOReturnSuccess else { fail("Bluetooth frame write failed (\(status)).") }
            offset += count
        }
        reply("OK")
    }
}

let link = Link()
let device = targetDevice()
link.start(device)
DispatchQueue.global().async {
    while let line = readLine() {
        let chars = Array(line.utf8)
        guard chars.count >= 2, chars.count <= 1024, chars.count % 2 == 0 else { fail("Invalid frame length.") }
        var bytes = [UInt8]()
        for i in stride(from: 0, to: chars.count, by: 2) {
            guard let b = UInt8(String(decoding: chars[i ... i+1], as: UTF8.self), radix: 16) else { fail("Invalid frame encoding.") }
            bytes.append(b)
        }
        let frame = bytes
        DispatchQueue.main.sync { link.writeFrame(frame) }
    }
    exit(0)
}
RunLoop.main.run()
