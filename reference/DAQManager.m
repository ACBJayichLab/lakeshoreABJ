classdef DAQManager < handle
    % class to configure all National Instruments DAQ input and output channels
    %
    % LTSPM #2 experimental setup: (the slot number refers to card slot in the chassis)
    % slot 1 - PXI internal
    % slot 2 - GPIB connection to SRS
    % slot 3 - 32 analog inputs, 4 analog outputs (top 2 breakout boxes)
    % slot 4 - 32 analog inputs, 4 analog outputs (breakout boxes 3&4)
    % slot 5 - 8 differential analog inputs, 2 analog outputs (breakout box 5)
    %
    % since the SRS is address via GPIB the GPIB address is defined here too
    
    
    events
        requestDAQ
    end
    
    properties
        DAQ;
        isDAQlocked = false;
        %         AnalogOutMinVoltages;
        %         AnalogOutMaxVoltages;
        defaultVoltage = 0;               % default output voltage e.g. for confocal intialization
    end
    
    properties (Constant)
        X = 1;
        Y = 2;
        Z = 3;
        % confocal XYZ output voltage lines:
        confocalX = 'PXI1Slot2/ao0';
        confocalY = 'PXI1Slot2/ao1';
        confocalZ = 'PXI1Slot2/ao2';

        %VVA Control
        vva = 4;
        vvaControl = 'PXI1Slot2/ao3';
        vvaMin = 0;
        vvaMax=10;

        % counter lines: (e.g. the "counter in" is PFI0 and trigger is PFI12)
        counterLine1 = '/PXI1Slot2/PFI0';
        counterLine2 = '/PXI1Slot2/PFI0';
        
        % trigger lines/clock lines:
        triggerLine1 = '/PXI1Slot2/PFI12'; % internal DAQ clock line/trigger
        triggerLine2 = '/PXI1Slot2/PFI13'; % external PulseBlaster counter TTL line
%         triggerLine3 = '/PXI1Slot3/PFI7';  % external PulseBlaster RF TTL line for counting up/down
        
        % GPIB addresses:
        srsGPIB = "GPIB0::24::INSTR";
        srsNuclearGPIB = "GPIB0::27::INSTR";
        srs3GPIB = "GPIB0::26::INSTR";
        lakeshore336 = "GPIB0::12::INSTR";
        lakeshore218 = "GPIB0::15::INSTR";
        
        %analog in for correlated counter, voltage measurement
        counterVoltageAI = 'PXI1Slot2/ai1';
        
        % photodiode analog input line:
        photoDiodeAI = 'PXI1Slot2/ai7';

        % powerMeter analog input line:
        powerMeterAI = 'PXI1Slot2/ai14';

        % not sure yet, what is going on with these:
        strDims = 'XYZ';
        CTR1 = 1;
        CTR2 = 2;
        CTR3 = 3;
        CLK1 = 1;
        CLK2 = 2;
        CLK3 = 3;
        %         ratioZtoX = 2.5;
        %         ratioYtoX = 1.0;


        %VOAcontrol = 'PXI1Slot2/ao3';
        %VOA=4;
        VOAdefaultVoltage = 2;
        
    end
    
    methods
        function obj = DAQManager(handles)
            % needs ImageScan handles, to e.g. get confocal piezo limits from ConfigureImageScan class
            %
            %             % unnecessary after software rewrite 2019 - SB
            %             obj.AnalogOutMinVoltages = [handles.configS.xMinVolts,handles.configS.yMinVolts,handles.configS.zMinVolts];
            %             obj.AnalogOutMaxVoltages = [handles.configS.xMaxVolts,handles.configS.yMaxVolts,handles.configS.zMaxVolts];
            
            
            % load the NIDAQ_Driver
            LibraryName = 'nidaqmx';
            LibraryFilePath = 'C:\WINDOWS\system32\nicaiu.dll'; % location of C header file for DLL.  Might need to modify due to unsupported datatypes
            HeaderFilePath = 'NIDAQmx.h';
            DeviceChannel = 'PXI1Slot2';
            % instantiate the driver
            obj.DAQ = NIDAQ_Driver(LibraryName,LibraryFilePath,HeaderFilePath,DeviceChannel);
            
            %Add Attenuator analog output lines
            %Obj.DAQ.addAOLine(obj.attenuatorOutput,obj.defaultVoltage,handles.configS.AnalogOutMinVoltages(obj.attenuator,handles.configS.AnalogOutMaxVoltages(obj.attenuator));
         
            % add confocal analog output lines
            obj.DAQ.addAOLine(obj.confocalX, obj.defaultVoltage, handles.configS.AnalogOutMinVoltages(obj.X), handles.configS.AnalogOutMaxVoltages(obj.X)); %X voltage
            obj.DAQ.addAOLine(obj.confocalY, obj.defaultVoltage, handles.configS.AnalogOutMinVoltages(obj.Y), handles.configS.AnalogOutMaxVoltages(obj.Y)); %Y voltage
            obj.DAQ.addAOLine(obj.confocalZ, obj.defaultVoltage, handles.configS.AnalogOutMinVoltages(obj.Z), handles.configS.AnalogOutMaxVoltages(obj.Z)); %Z voltage
           
            %Add VVA control line
            obj.DAQ.addAOLine(obj.vvaControl, obj.defaultVoltage, obj.vvaMin, obj.vvaMax); %VOA voltage


            % add VOA control line
            %obj.DAQ.addAOLine(obj.VOAcontrol, obj.VOAdefaultVoltage, handles.VOAControl.VOAMinVolts, handles.VOAControl.VOAMaxVolts); %VOA voltage
            
            % set all output lines to default voltage upon opening ImageScan
            obj.DAQ.WriteAnalogOutAllLines;
            
            
            % add photoDiode analog input line
            obj.DAQ.addAILine(obj.counterVoltageAI);
            obj.DAQ.addAILine(obj.photoDiodeAI);
            
            %add powerMeter analog input line
            obj.DAQ.addAILine(obj.powerMeterAI);
            
            %See "Buffered (Sample Clock) Edge Counting"

            % add Clock Lines
            obj.DAQ.addClockLine('PXI1Slot2/ctr0', obj.triggerLine1);
            obj.DAQ.addClockLine('PXI1Slot2/ctr1', obj.triggerLine2);
%             obj.DAQ.addClockLine('PIXISlot3/ctr3', obj.triggerLine3); % for up/down counting
            
            
            % add Counter Lines
            obj.DAQ.addCounterInLine('PXI1Slot2/ctr2', obj.counterLine1, DAQManager.CLK1);%Ctr1
            obj.DAQ.addCounterInLine('PXI1Slot2/ctr3', obj.counterLine2, DAQManager.CLK2);

        end
        
        
        
        function delete(obj)
            obj.DAQ.AnalogOutVoltages = zeros(length(obj.DAQ.AnalogOutVoltages));
            obj.DAQ.WriteAnalogOutAllLines();
            obj.DAQ.delete();
        end
    end
    
end

