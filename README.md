# Nexus - Using Kademlia Algorithm

Nexus is a robust networking application built on the Kademlia Distributed Hash Table (DHT) protocol. It provides secure node discovery, routing, file sharing capabilities, and a real-time network visualization dashboard. The project is designed for scalability, security, and ease of use, featuring a modern PyQt6 GUI interface.

## Features

### Core Networking
- **Kademlia DHT Implementation**: Distributed hash table for efficient node lookup and data storage
- **Secure Communication**: End-to-end encryption using secp256k1 elliptic curve cryptography
- **Automatic Peer Discovery**: LAN broadcasting for seamless network joining
- **Robust Routing**: XOR-based routing table with K-bucket management

### File Sharing
- **DHT-Based Storage**: Decentralized file storage and retrieval
- **File Metadata Management**: Efficient indexing and searching of shared files
- **Download Management**: Background file transfer with progress tracking

### User Interface
- **Real-time Network Graph**: Interactive visualization of the P2P network topology
- **Node Status Dashboard**: Comprehensive monitoring of node health and connections
- **Event Logging**: Real-time activity feed with color-coded message types
- **Bootstrap Controls**: Manual peer addition and network management

### Upcoming Features
- **Chat System**: Real-time messaging between network participants
- **Enhanced Security**: Additional cryptographic protocols
- **Mobile Support**: Cross-platform compatibility

## Architecture

The application consists of several key components:

- `network.py`: Core Kademlia protocol implementation
- `dht_storage.py`: DHT-based data storage and retrieval
- `discovery.py`: Peer discovery mechanisms
- `routing.py`: XOR-based routing algorithms
- `crypto.py`: Cryptographic utilities and key management
- `gui.py`: Main PyQt6 interface
- `graph_widget.py`: Network visualization component
- `file_sharing_gui.py`: File sharing interface
- `protocols.py`: Network protocol definitions

## Team Structure

- **Harsh & Krish**: Continue with network graph visualization
- **Mangesh**: Core networking + Chat message routing and protocol implementation
- **Nitin**: Core networking + Chat encryption and security
- **Jagannadha**: UI/File sharing + Chat integration with main GUI
- **Kush**: UI/File sharing + Chat UI components

## Installation

### Prerequisites
- Python 3.8 or higher
- pip package manager

### Dependencies
Install required packages using pip:

```bash
pip install -r requirements.txt
```

Required packages:
- coincurve>=18.0.0 (secp256k1 cryptography)
- PyQt6>=6.6.0 (desktop GUI)

### Running the Application

#### Windows
Double-click `start_windows.bat` or run:

```bash
python app.py
```

#### Linux/Mac
```bash
python3 app.py
```

### Command Line Options

- `--host`: IP address to bind (default: 0.0.0.0)
- `--port`: UDP port to listen on (default: auto-select)
- `--key`: Path to persisted private key file
- `--save-key`: Save generated key to file
- `--bootstrap`: Bootstrap peer addresses (HOST:PORT format)
- `--no-discovery`: Disable LAN peer discovery

Example:
```bash
python app.py --bootstrap 192.168.1.100:9000 --save-key mykey.hex
```
WorkFlow
<img width="746" height="1196" alt="image" src="https://github.com/user-attachments/assets/dcefa16f-0dc4-45d1-8e42-d35234d4a935" />

## Usage

### First Time Setup
1. Launch the application
2. The GUI will display the network graph and node status
3. If no peers are found, use bootstrap addresses or wait for LAN discovery

### Network Visualization
- View real-time network topology in the graph panel
- Nodes are arranged in a circular layout
- Edges represent connections, colored by XOR distance
- Animated packets show network activity

### File Sharing
- Access the file sharing tab in the GUI
- Share files by adding them to the DHT
- Search and download files from the network
- Monitor transfer progress in real-time

### Node Management
- View routing table in the buckets panel
- Monitor node health and connection status
- Add manual bootstrap peers if needed

## Development

### Project Structure
```
nexus-p2p/
├── app.py                 # Main application entry point
├── network.py             # Core Kademlia implementation
├── gui.py                 # PyQt6 user interface
├── graph_widget.py        # Network visualization
├── dht_storage.py         # DHT storage layer
├── file_manager.py        # File operations
├── crypto.py              # Cryptographic utilities
├── discovery.py           # Peer discovery
├── routing.py             # Routing algorithms
├── protocols.py           # Network protocols
├── rpc_extensions.py      # RPC extensions
├── requirements.txt       # Python dependencies
├── README.md              # This file
└── start_windows.bat      # Windows launcher
```

### Adding the Chat System

The chat system will be implemented as follows:

1. **Message Protocol**: Extend existing RPC with chat message types
2. **UI Integration**: Add chat panel to the main GUI
3. **Encryption**: Use existing crypto module for message security
4. **Storage**: Store chat history in DHT for persistence

### Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

### Code Style
- Follow PEP 8 guidelines
- Use type hints for function signatures
- Add docstrings to all public functions
- Keep functions focused and modular

## Security

- All network communications are encrypted using secp256k1
- Private keys can be persisted securely
- DHT operations include integrity verification
- No central authority or single point of failure

## Performance

- Optimized for low-latency P2P operations
- Efficient XOR-based routing (O(log n) complexity)
- Real-time GUI updates with 30fps visualization
- Minimal memory footprint for long-running nodes

## Troubleshooting

### Common Issues

**Can't connect to network:**
- Check firewall settings for UDP port access
- Verify bootstrap peer addresses
- Ensure LAN discovery is enabled

**GUI not responding:**
- Check PyQt6 installation
- Verify display environment variables
- Try running with `--no-discovery` for testing

**File sharing slow:**
- Check network bandwidth
- Verify DHT health in the status panel
- Ensure sufficient peer connections

### Logs
Application logs are written to console with timestamps. Use the event log in the GUI for real-time monitoring.

## License

This project is open source. See LICENSE file for details.

## Acknowledgments

- Based on the Kademlia DHT paper by Petar Maymounkov and David Mazières
- Built with PyQt6 for the desktop interface
- Uses coincurve for high-performance cryptography

---

**Note**: This project is under active development. Features and APIs may change.
