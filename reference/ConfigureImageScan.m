
classdef ConfigureImageScan < handle
    % confocal calibration and max voltages - known as "configS"
    
    
    properties
        bAutoSave =     1;          % autosave images
        bHaveInverterBoard = 0;     % inverter board      
%         bHaveNanoscopeAFM = 0;      % AFM
%         numUSBFilterWheels = 1;     % 
%         bHaveZurichInstr = 1;       % Zurich Instruments
%         bHaveMCLXYZScanner = 1;     %
%         bMagnetGui = 0;             %
        
        dataFolder  =    'C:\Coding\ImageData\2019_Apr_25\';
%         sequenceFolder = 'C:\Users\lab\Documents\MATLAB\ImageScan_v2019\PulseBlaster\PulseSequences\';
        sequenceFolder = 'C:\Coding\LTSPM3\PulseBlaster\PulseSequences\';
 
        % confocal piezo calibration
%         xScanMicronsPerVolt =   6;     % x [mu/V], old definition removed with v2019
%         yScanMicronsPerVolt =   6;     % y [mu/V]
%         zScanMicronsPerVolt =   12.5;  % z [mu/V]
        %micronsPerVoltX =    10.517;      % x [mu/V];
        %micronsPerVoltY =    10.517;      % y [mu/V];
        micronsPerVoltX = 10.947
        micronsPerVoltY = 7.498598
        micronsPerVoltZ =   10;    % z [mu/V]; 
        voltsPerMicronX;
        voltsPerMicronY;
        voltsPerMicronZ;     
        
        % confocal piezo limits 
        % (FYI: best to choose X and Y voltages the same, as mNIDAQ_Driver
        % will set both limits to the smaller one in e.g. an xy scan)
        xMinVolts = -9.95;      
        xMaxVolts =  9.95;
        yMinVolts = -9.95;
        yMaxVolts =  9.95;
        zMinVolts =  -10;
        zMaxVolts =  10;

        % Offsets to make the mirror vertical in
        xOffsetVolts = 0;
        yOffsetVolts = 0;
        zOffsetVolts = 0;

        AnalogOutMinVoltages;
        AnalogOutMaxVoltages;
                      
        % photo diode calibration
        bPhotoDiode = false;
        photoDiodeConversion =  103;     % [uW/V]
        photoDiodeDark =        0;     % [V]
        
        % other parameters
        imageScanBGColorR = 228;
        imageScanBGColorG = 240;
        imageScanBGColorB = 230;
        
        
    end %properties
    
    methods 
        
          function obj = ConfigureImageScan()
            
            % these are a copy of the calibration parameters from the ConfigureImageScan class:
            obj.voltsPerMicronX = 1/obj.micronsPerVoltX;    % inverse
            obj.voltsPerMicronY = 1/obj.micronsPerVoltY;    % inverse
            obj.voltsPerMicronZ = 1/obj.micronsPerVoltZ;    % inverse
            
            % restructuring to allow easy dimension read out
            obj.AnalogOutMinVoltages = [obj.xMinVolts, obj.yMinVolts, obj.zMinVolts];
            obj.AnalogOutMaxVoltages = [obj.xMaxVolts, obj.yMaxVolts, obj.zMaxVolts];
            
        end
    end
    
end