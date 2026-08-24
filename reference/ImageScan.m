function varargout = ImageScan(varargin)
% IMAGESCAN MATLAB code for ImageScan.fig
%      IMAGESCAN, by itself, creates a new IMAGESCAN or raises the existing
%      singleton*.
%
%      H = IMAGESCAN returns the handle to a new IMAGESCAN or the handle to
%      the existing singleton*.
%
%      IMAGESCAN('CALLBACK',hObject,eventData,handles,...) calls the local
%      function named CALLBACK in IMAGESCAN.M with the given input arguments.
%
%      IMAGESCAN('Property','Value',...) creates a new IMAGESCAN or raises the
%      existing singleton*.  Starting from the left, property value pairs are
%      applied to the GUI before ImageScan_OpeningFcn gets called.  An
%      unrecognized property name or invalid value makes property application
%      stop.  All inputs are passed to ImageScan_OpeningFcn via varargin.
%
%      *See GUI Options on GUIDE's Tools menu.  Choose "GUI allows only one
%      instance to run (singleton)".
%
% See also: GUIDE, GUIDATA, GUIHANDLES

% Edit the above text to modify the response to help ImageScan

% Last Modified by GUIDE v2.5 22-Aug-2024 17:28:02
% This is a new version of ImageScan to transfer to new Matlab versions
% (past R2015b) - created based previous Matlab GUI with improved
% functionality. (June 2017, SB)

% Begin initialization code - DO NOT EDIT
gui_Singleton = 1;
gui_State = struct('gui_Name',       mfilename, ...
                   'gui_Singleton',  gui_Singleton, ...
                   'gui_OpeningFcn', @ImageScan_OpeningFcn, ...
                   'gui_OutputFcn',  @ImageScan_OutputFcn, ...
                   'gui_LayoutFcn',  [] , ...
                   'gui_Callback',   []);
if nargin && ischar(varargin{1})
    gui_State.gui_Callback = str2func(varargin{1});
end

if nargout
    [varargout{1:nargout}] = gui_mainfcn(gui_State, varargin{:});
else
    gui_mainfcn(gui_State, varargin{:});
end
% End initialization code - DO NOT EDIT
end

% --- Executes just before ImageScan is made visible.
function ImageScan_OpeningFcn(hObject, ~, handles, varargin)
% This function has no output args, see OutputFcn.
% hObject    handle to figure
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)
% varargin   command line arguments to ImageScan (see VARARGIN)

    % Choose default command line output for ImageScan
    handles.output = hObject;
    
    handles.version = 2019.04; % software version is in YYYY.MM

    
%     global laser_x_handle;
%     laser_x_handle = handles.editPositionX;
%     global laser_y_handle;
%     laser_y_handle = handles.editPositionY;
%     global laser_z_handle;
%     laser_z_handle = handles.editPositionZ;
    

    %%%% confocal scan setup (load calibrations, default scan size, etc.)
    handles.configS = ConfigureImageScan();             % load calibration for confocal scan
    handles.ScanParameters = ConfocalScanParameters(handles);      % structure with current confocal scan parameters (is continuously updated with e.g. scan size, scan direction, etc.)
    handles.TrackingParameters = ConfocalScanTrackingParameters(handles); % structure with current NV tracking parameters

    %%%% start pulseBlaster
    handles.PulseInterpreter = PulseInterpreter();
    handles.PulseInterpreter.clearPulseblaster();

    %Start up VOA
    handles.VOAControl = VOAControl();
    handles.SatCurveParameters = SaturationCurveParameters();

%   handles.bSimulatedData = false;
    %%%% load the NIDAQ
    handles.DAQManager = DAQManager(handles);

    %Initialize the VOA to default voltage
    %handles.VOAControl.initializeVOA(handles);

    %%% load the confocal control classes
    handles.ScanControl = ScanControl(handles);
    handles.CursorControl = CursorControl();
    handles.StateControl = StateControl();
    handles.StateControl.initializeState(handles); % initialize the state machine
    handles.CursorControl.initializePosition(handles);
    
    %%%% start up SRS
    % Regular SRS
    handles.srs = visadev(handles.DAQManager.srsGPIB);
    % Nuclear SRS
    handles.srsNuclear = visadev(handles.DAQManager.srsNuclearGPIB);
    handles.srs3 = visadev(handles.DAQManager.srs3GPIB);
    %handles.srs3 = NaN;
    %%%% Start up lakeshore 218 temp monitor
    %handles.lakeshore218 = visadev(handles.DAQManager.lakeshore218);

    %%% load ESR control
    handles.ESRControl = [];
    handles.EsrGlobalMethods = EsrGlobalMethods(handles.DAQManager.DAQ,...
        handles.PulseInterpreter,handles.srs,handles.srsNuclear,handles.srs3,handles);
    handles.EsrCWSweep = EsrCWSweep(handles,handles.DAQManager.DAQ,...
        handles.PulseInterpreter,handles.srs,handles.srsNuclear,handles.srs3,handles.EsrGlobalMethods);
    handles.EsrPulsedSweep = EsrPulsedSweep(handles,handles.DAQManager.DAQ,...
        handles.PulseInterpreter,handles.srs,handles.srsNuclear,handles.srs3,handles.EsrGlobalMethods);
    handles.EsrAutomation = EsrAutomation(handles);
    %%%%%%%%%%%%%%
    
    
    % initize NV Marker List
    handles.maxLabel = 50;
    handles.listNVMarkers = zeros(1,handles.maxLabel);
    handles.listNVMarkerPos = zeros(3,handles.maxLabel); %x,y,z
    handles.listNVMarkerType = zeros(1,handles.maxLabel);
    
    %global ESR_pulsed_handles;
    %ESR_pulsed_handles = handles.EsrPulsedSweep;
    
    % disable SavePath editing and SaveFileNumber editing, because the
    % program will update the data folder and file number automatically
    handles.inputSaveImagePath.String = handles.configS.dataFolder;    
    set(handles.inputSaveImagePath,'Enable','off')
    set(handles.inputSaveImageFileNum,'Enable','off')
    
    % set the colorbar and plot axis
    colorm.cmap = pink(64);
    colorm.auto = true;
    set(handles.confocalAxes, 'UserData', colorm);
    handles.panFigure = pan(hObject);
    set(handles.panFigure, 'Enable', 'on');
    set(hObject,'Colormap',colorm.cmap);
    colorbar('peer', handles.confocalAxes);
    xlabel(handles.confocalAxes,'X [µm]');
    ylabel(handles.confocalAxes,'Y [µm]');
    set(handles.axesCountHistory.YLabel,'String','[kcounts/s]');
    clear colorm
       
    % % load saved sample and tip specifications
    % if exist('currentSample.mat','file')~=2
    %  currentSample.sample = 'sample';
    %  currentSample.tip = 'tip';
    %  save('currentSample')
    % end
    % disp("VISA resource mat error Line 147 of ImageScan")
    % load('C:/Coding/LTSPM3/currentSample.mat');
    % set(handles.sample,'String',currentSample.sample)
    % set(handles.tipDiamond,'String',currentSample.tip)
    % clear currentSample
    
%     % ----- start Approach GUI ------------------------------------------
%     % which will also connect to Zurich Instruments lock-in 
%     % start ApproachGUI
%     handles.ApproachGUI = ApproachGUI();
%     handles.ApproachGUIHandles = guidata(handles.ApproachGUI);
%     handles.approaching = false;
    
%     % start TransportGUI
%     global measureTransportCont;
%     measureTransportCont = true;
%     handles.TransportRate = 100000;
%     handles.TransportDataLength = 500;
%     handles.TransportMeasureTime = linspace(0, handles.TransportDataLength/handles.TransportRate, handles.TransportDataLength);
%     handles.TransportGUI = TransportGUI();
%     handles.TransportGUIHandles = guidata(handles.TransportGUI);
    
    % Start Vector Magnet Control
    % See python methods by calling: methods(handles.vectorMagnet)
    path_add = fileparts(which('vectorMagnet.py'));
    if count(py.sys.path, path_add) == 0
        insert(py.sys.path, int64(0), path_add);
    end
    pyenv(ExecutionMode="OutOfProcess");
    vectorMagnetModule=py.importlib.import_module('vectorMagnet');
    handles.vectorMagnet = vectorMagnetModule.vectorMagnet();
    handles.vectorMagnet.initialize_program()
    disp("initialized waiting to connect")
    pause(1)
    stateValue = handles.vectorMagnet.connect();
    if(stateValue ~= 3)
        disp("Connection not established to Multi-Axis")
    end
    handles.imgTrackFig = figure("Name",'Image Registration Tracking','NumberTitle', 'off', 'Tag', 'imgTrackFigure');
    % 
    % Update handles structure
    guidata(hObject, handles);
    handles.guiTimer = timer('ExecutionMode', 'fixedSpacing', 'Period', .25, 'TimerFcn', {@updateGUIsTimer, hObject}, 'Name', 'gui_timer');

    % Update handles structure
    guidata(hObject, handles);
    global Img_handles;
    Img_handles = guidata(hObject);
%     pause(0.05)
    start(handles.guiTimer);
    
    % UIWAIT makes ImageScan wait for user response (see UIRESUME)
    % uiwait(handles.ImageScan);
    
end

% --- Outputs from this function are returned to the command line.
function varargout = ImageScan_OutputFcn(~, ~, handles)
% varargout  cell array for returning output args (see VARARGOUT);
% hObject    handle to figure
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

% Get default command line output from handles structure
varargout{1} = handles.output;
end


% --- Executes on button press in toggleGreenAOM.
function toggleGreenAOM_Callback(hObject,~, handles) %#ok<*DEFNU>
% hObject    handle to toggleGreenAOM (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)
greenState = get(hObject,'Value');
yellowState = get(handles.toggleYellowAOM, 'Value');
redState = get(handles.toggleRedAOM, 'Value');
sequencePath=[handles.configS.sequenceFolder 'AOMButtonSequences\'];
switch greenState
    case true
        switch yellowState
            case true
                switch redState
                    case true
                        handles.PulseInterpreter.clearPulseblaster();
                        handles.PulseInterpreter.setCurrentPulse([sequencePath 'greenyellowredAOM_on.txt']);
                        handles.PulseInterpreter.loadToPulseblaster();
                        handles.PulseInterpreter.runPulse();
                        set(handles.toggleGreenAOM,'String','Turn Off');
                    case false
                        handles.PulseInterpreter.clearPulseblaster();
                        handles.PulseInterpreter.setCurrentPulse([sequencePath 'greenyellowAOM_on.txt']);
                        handles.PulseInterpreter.loadToPulseblaster();
                        handles.PulseInterpreter.runPulse();
                        set(handles.toggleGreenAOM,'String','Turn Off');
                end
            case false
                switch redState
                    case true
                        handles.PulseInterpreter.clearPulseblaster();
                        handles.PulseInterpreter.setCurrentPulse([sequencePath 'greenredAOM_on.txt']);
                        handles.PulseInterpreter.loadToPulseblaster();
                        handles.PulseInterpreter.runPulse();
                        set(handles.toggleGreenAOM,'String','Turn Off');
                    case false
                        handles.PulseInterpreter.clearPulseblaster();
                        handles.PulseInterpreter.setCurrentPulse([sequencePath 'greenAOM_on.txt']);
                        handles.PulseInterpreter.loadToPulseblaster();
                        handles.PulseInterpreter.runPulse();
                        set(handles.toggleGreenAOM,'String','Turn Off');
                end
        end
    case false
        switch yellowState
            case true
                switch redState
                    case true
                        handles.PulseInterpreter.clearPulseblaster();
                        handles.PulseInterpreter.setCurrentPulse([sequencePath 'yellowredAOM_on.txt']);
                        handles.PulseInterpreter.loadToPulseblaster();
                        handles.PulseInterpreter.runPulse();
                        set(handles.toggleGreenAOM, 'String', 'Turn On');
                    case false
                        handles.PulseInterpreter.clearPulseblaster();
                        handles.PulseInterpreter.setCurrentPulse([sequencePath 'yellowAOM_on.txt']);
                        handles.PulseInterpreter.loadToPulseblaster();
                        handles.PulseInterpreter.runPulse();
                        set(handles.toggleGreenAOM, 'String', 'Turn On');
                end
            case false
                switch redState
                    case true
                        handles.PulseInterpreter.clearPulseblaster();
                        handles.PulseInterpreter.setCurrentPulse([sequencePath 'redAOM_on.txt']);
                        handles.PulseInterpreter.loadToPulseblaster();
                        handles.PulseInterpreter.runPulse();
                        set(handles.toggleGreenAOM, 'String', 'Turn On');
                    case false
                        % turn green laser off
                        handles.PulseInterpreter.clearPulseblaster();
                        set(handles.toggleGreenAOM, 'String', 'Turn On'); 
                end
        end
end

end


% --- Executes on button press in toggleYellowAOM.
function toggleYellowAOM_Callback(hObject, ~, handles)
% hObject    handle to toggleYellowAOM (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

% hObject    handle to toggleGreenAOM (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)
greenState = get(handles.toggleGreenAOM, 'Value');
yellowState = get(hObject,'Value');
redState = get(handles.toggleRedAOM, 'Value');
sequencePath=[handles.configS.sequenceFolder 'AOMButtonSequences\'];
switch greenState
    case true
        switch yellowState
            case true
                switch redState
                    case true
                        handles.PulseInterpreter.clearPulseblaster();
                        handles.PulseInterpreter.setCurrentPulse([sequencePath 'greenyellowredAOM_on.txt']);
                        handles.PulseInterpreter.loadToPulseblaster();
                        handles.PulseInterpreter.runPulse();
                        set(handles.toggleYellowAOM,'String','Turn Off');
                    case false
                        handles.PulseInterpreter.clearPulseblaster();
                        handles.PulseInterpreter.setCurrentPulse([sequencePath 'greenyellowAOM_on.txt']);
                        handles.PulseInterpreter.loadToPulseblaster();
                        handles.PulseInterpreter.runPulse();
                        set(handles.toggleYellowAOM,'String','Turn Off');
                end
            case false
                switch redState
                    case true
                        handles.PulseInterpreter.clearPulseblaster();
                        handles.PulseInterpreter.setCurrentPulse([sequencePath 'greenredAOM_on.txt']);
                        handles.PulseInterpreter.loadToPulseblaster();
                        handles.PulseInterpreter.runPulse();
                        set(handles.toggleYellowAOM, 'String', 'Turn On');
                    case false
                        handles.PulseInterpreter.clearPulseblaster();
                        handles.PulseInterpreter.setCurrentPulse([sequencePath 'greenAOM_on.txt']);
                        handles.PulseInterpreter.loadToPulseblaster();
                        handles.PulseInterpreter.runPulse();
                        set(handles.toggleYellowAOM, 'String', 'Turn On');
                end
        end
    case false
        switch yellowState
            case true
                switch redState
                    case true
                        handles.PulseInterpreter.clearPulseblaster();
                        handles.PulseInterpreter.setCurrentPulse([sequencePath 'yellowredAOM_on.txt']);
                        handles.PulseInterpreter.loadToPulseblaster();
                        handles.PulseInterpreter.runPulse();
                        set(handles.toggleYellowAOM,'String','Turn Off');
                    case false
                        handles.PulseInterpreter.clearPulseblaster();
                        handles.PulseInterpreter.setCurrentPulse([sequencePath 'yellowAOM_on.txt']);
                        handles.PulseInterpreter.loadToPulseblaster();
                        handles.PulseInterpreter.runPulse();
                        set(handles.toggleYellowAOM,'String','Turn Off');
                end
            case false
                switch redState
                    case true
                        handles.PulseInterpreter.clearPulseblaster();
                        handles.PulseInterpreter.setCurrentPulse([sequencePath 'redAOM_on.txt']);
                        handles.PulseInterpreter.loadToPulseblaster();
                        handles.PulseInterpreter.runPulse();
                        set(handles.toggleYellowAOM, 'String', 'Turn On');
                    case false
                        % turn yellow laser off
                        handles.PulseInterpreter.clearPulseblaster();
                        set(handles.toggleYellowAOM, 'String', 'Turn On'); 
                end
        end
end
end

% --- Executes on button press in toggleRedAOM.
function toggleRedAOM_Callback(hObject, eventdata, handles)
% hObject    handle to toggleGreenAOM (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)
greenState = get(handles.toggleGreenAOM, 'Value');
yellowState = get(handles.toggleYellowAOM, 'Value');
redState = get(hObject,'Value');
sequencePath=[handles.configS.sequenceFolder 'AOMButtonSequences\'];
switch greenState
    case true
        switch yellowState
            case true
                switch redState
                    case true
                        handles.PulseInterpreter.clearPulseblaster();
                        handles.PulseInterpreter.setCurrentPulse([sequencePath 'greenyellowredAOM_on.txt']);
                        handles.PulseInterpreter.loadToPulseblaster();
                        handles.PulseInterpreter.runPulse();
                        set(handles.toggleRedAOM,'String','Turn Off');
                    case false
                        handles.PulseInterpreter.clearPulseblaster();
                        handles.PulseInterpreter.setCurrentPulse([sequencePath 'greenyellowAOM_on.txt']);
                        handles.PulseInterpreter.loadToPulseblaster();
                        handles.PulseInterpreter.runPulse();
                        set(handles.toggleRedAOM, 'String', 'Turn On');
                end
            case false
                switch redState
                    case true
                        handles.PulseInterpreter.clearPulseblaster();
                        handles.PulseInterpreter.setCurrentPulse([sequencePath 'greenredAOM_on.txt']);
                        handles.PulseInterpreter.loadToPulseblaster();
                        handles.PulseInterpreter.runPulse();
                        set(handles.toggleRedAOM,'String','Turn Off');
                    case false
                        handles.PulseInterpreter.clearPulseblaster();
                        handles.PulseInterpreter.setCurrentPulse([sequencePath 'greenAOM_on.txt']);
                        handles.PulseInterpreter.loadToPulseblaster();
                        handles.PulseInterpreter.runPulse();
                        set(handles.toggleRedAOM, 'String', 'Turn On');
                end
        end
    case false
        switch yellowState
            case true
                switch redState
                    case true
                        handles.PulseInterpreter.clearPulseblaster();
                        handles.PulseInterpreter.setCurrentPulse([sequencePath 'yellowredAOM_on.txt']);
                        handles.PulseInterpreter.loadToPulseblaster();
                        handles.PulseInterpreter.runPulse();
                        set(handles.toggleRedAOM,'String','Turn Off');
                    case false
                        handles.PulseInterpreter.clearPulseblaster();
                        handles.PulseInterpreter.setCurrentPulse([sequencePath 'yellowAOM_on.txt']);
                        handles.PulseInterpreter.loadToPulseblaster();
                        handles.PulseInterpreter.runPulse();
                        set(handles.toggleRedAOM, 'String', 'Turn On');
                end
            case false
                switch redState
                    case true
                        handles.PulseInterpreter.clearPulseblaster();
                        handles.PulseInterpreter.setCurrentPulse([sequencePath 'redAOM_on.txt']);
                        handles.PulseInterpreter.loadToPulseblaster();
                        handles.PulseInterpreter.runPulse();
                        set(handles.toggleRedAOM,'String','Turn Off');
                    case false
                        % turn yellow laser off
                        handles.PulseInterpreter.clearPulseblaster();
                        set(handles.toggleRedAOM, 'String', 'Turn On'); 
                end
        end
end
end

function editPositionX_Callback(~, ~, handles)
% hObject    handle to editPositionX (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

% Hints: get(hObject,'String') returns contents of editPositionX as text
%        str2double(get(hObject,'String')) returns contents of editPositionX as a double
    handles.CursorControl.updatePositionFromEdit(handles,handles.editPositionX,1,0.0);
end

% --- Executes during object creation, after setting all properties.
function editPositionX_CreateFcn(hObject, ~, ~)
% hObject    handle to editPositionX (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    empty - handles not created until after all CreateFcns called

% Hint: edit controls usually have a white background on Windows.
%       See ISPC and COMPUTER.
if ispc && isequal(get(hObject,'BackgroundColor'), get(0,'defaultUicontrolBackgroundColor'))
    set(hObject,'BackgroundColor','white');
end
end

% --- Executes on button press in buttonIncrementX.
function buttonIncrementX_Callback(~, ~, handles)
% hObject    handle to buttonIncrementX (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)
    increment = str2double(get(handles.editStepSizeXY,'String'));
    handles.CursorControl.updatePositionFromEdit(handles,handles.editPositionX,1,increment);
end

% --- Executes on button press in buttonDecrementX.
function buttonDecrementX_Callback(~, ~, handles)
% hObject    handle to buttonDecrementX (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)
    decrement = -str2double(get(handles.editStepSizeXY,'String'));
    handles.CursorControl.updatePositionFromEdit(handles,handles.editPositionX,1,decrement);
end


function editPositionY_Callback(~, ~, handles)
% hObject    handle to editPositionY (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

% Hints: get(hObject,'String') returns contents of editPositionY as text
%        str2double(get(hObject,'String')) returns contents of editPositionY as a double
    handles.CursorControl.updatePositionFromEdit(handles,handles.editPositionY,2,0.0);
end

% --- Executes during object creation, after setting all properties.
function editPositionY_CreateFcn(hObject, ~, ~)
% hObject    handle to editPositionY (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    empty - handles not created until after all CreateFcns called

% Hint: edit controls usually have a white background on Windows.
%       See ISPC and COMPUTER.
if ispc && isequal(get(hObject,'BackgroundColor'), get(0,'defaultUicontrolBackgroundColor'))
    set(hObject,'BackgroundColor','white');
end
end

% --- Executes on button press in buttonIncrementY.
function buttonIncrementY_Callback(~, ~, handles)
% hObject    handle to buttonIncrementY (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)
    increment = str2double(get(handles.editStepSizeXY,'String'));
    handles.CursorControl.updatePositionFromEdit(handles,handles.editPositionY,2,increment);
    clear increment
end

% --- Executes on button press in buttonDecrementY.
function buttonDecrementY_Callback(~, ~, handles)
% hObject    handle to buttonDecrementY (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)
    decrement = -str2double(get(handles.editStepSizeXY,'String'));
    handles.CursorControl.updatePositionFromEdit(handles,handles.editPositionY,2,decrement);
    clear decrement
end


function editPositionZ_Callback(~, ~, handles)
% hObject    handle to editPositionZ (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

% Hints: get(hObject,'String') returns contents of editPositionZ as text
%        str2double(get(hObject,'String')) returns contents of editPositionZ as a double
    handles.CursorControl.updatePositionFromEdit(handles,handles.editPositionZ,3,0.0);
end

% --- Executes during object creation, after setting all properties.
function editPositionZ_CreateFcn(hObject, ~, ~)
% hObject    handle to editPositionZ (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    empty - handles not created until after all CreateFcns called

% Hint: edit controls usually have a white background on Windows.
%       See ISPC and COMPUTER.
if ispc && isequal(get(hObject,'BackgroundColor'), get(0,'defaultUicontrolBackgroundColor'))
    set(hObject,'BackgroundColor','white');
end
end

% --- Executes on button press in buttonIncrementZ.
function buttonIncrementZ_Callback(~, ~, handles)
% hObject    handle to buttonIncrementZ (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)
    increment = str2double(get(handles.editStepSizeZ,'String'));
    handles.CursorControl.updatePositionFromEdit(handles,handles.editPositionZ,3,increment);
    clear increment
end

% --- Executes on button press in buttonDecrementZ.
function buttonDecrementZ_Callback(~, ~, handles)
% hObject    handle to buttonDecrementZ (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)
    decrement = -str2double(get(handles.editStepSizeZ,'String'));
    handles.CursorControl.updatePositionFromEdit(handles,handles.editPositionZ,3,decrement);
    clear decrement
end

function editStepSizeXY_Callback(~, ~, ~)
% hObject    handle to editStepSizeXY (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

% Hints: get(hObject,'String') returns contents of editStepSizeXY as text
%        str2double(get(hObject,'String')) returns contents of editStepSizeXY as a double
end

% --- Executes during object creation, after setting all properties.
function editStepSizeXY_CreateFcn(hObject, ~, ~)
% hObject    handle to editStepSizeXY (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    empty - handles not created until after all CreateFcns called

% Hint: edit controls usually have a white background on Windows.
%       See ISPC and COMPUTER.
if ispc && isequal(get(hObject,'BackgroundColor'), get(0,'defaultUicontrolBackgroundColor'))
    set(hObject,'BackgroundColor','white');
end
end

function editStepSizeZ_Callback(~, ~, ~)
% hObject    handle to editStepSizeZ (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

% Hints: get(hObject,'String') returns contents of editStepSizeZ as text
%        str2double(get(hObject,'String')) returns contents of editStepSizeZ as a double
end

% --- Executes during object creation, after setting all properties.
function editStepSizeZ_CreateFcn(hObject, ~, ~)
% hObject    handle to editStepSizeZ (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    empty - handles not created until after all CreateFcns called

% Hint: edit controls usually have a white background on Windows.
%       See ISPC and COMPUTER.
if ispc && isequal(get(hObject,'BackgroundColor'), get(0,'defaultUicontrolBackgroundColor'))
    set(hObject,'BackgroundColor','white');
end
end

function inputSaveImagePath_Callback(~, ~, ~)
% hObject    handle to inputSaveImagePath (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

% Hints: get(hObject,'String') returns contents of inputSaveImagePath as text
%        str2double(get(hObject,'String')) returns contents of inputSaveImagePath as a double
end

% --- Executes during object creation, after setting all properties.
function inputSaveImagePath_CreateFcn(hObject, ~, ~)
% hObject    handle to inputSaveImagePath (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    empty - handles not created until after all CreateFcns called

% Hint: edit controls usually have a white background on Windows.
%       See ISPC and COMPUTER.
if ispc && isequal(get(hObject,'BackgroundColor'), get(0,'defaultUicontrolBackgroundColor'))
    set(hObject,'BackgroundColor','white');
end
end

function inputSaveImageFilePrefix_Callback(~, ~, ~)
% hObject    handle to inputSaveImageFilePrefix (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

% Hints: get(hObject,'String') returns contents of inputSaveImageFilePrefix as text
%        str2double(get(hObject,'String')) returns contents of inputSaveImageFilePrefix as a double
end

% --- Executes during object creation, after setting all properties.
function inputSaveImageFilePrefix_CreateFcn(hObject, ~, ~)
% hObject    handle to inputSaveImageFilePrefix (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    empty - handles not created until after all CreateFcns called

% Hint: edit controls usually have a white background on Windows.
%       See ISPC and COMPUTER.
if ispc && isequal(get(hObject,'BackgroundColor'), get(0,'defaultUicontrolBackgroundColor'))
    set(hObject,'BackgroundColor','white');
end
end

function inputSaveImageFileNum_Callback(~, ~, ~)
% hObject    handle to inputSaveImageFileNum (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

% Hints: get(hObject,'String') returns contents of inputSaveImageFileNum as text
%        str2double(get(hObject,'String')) returns contents of inputSaveImageFileNum as a double
end

% --- Executes during object creation, after setting all properties.
function inputSaveImageFileNum_CreateFcn(hObject, ~, ~)
% hObject    handle to inputSaveImageFileNum (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    empty - handles not created until after all CreateFcns called

% Hint: edit controls usually have a white background on Windows.
%       See ISPC and COMPUTER.
if ispc && isequal(get(hObject,'BackgroundColor'), get(0,'defaultUicontrolBackgroundColor'))
    set(hObject,'BackgroundColor','white');
end
end

function tipDiamond_Callback(hObject, ~, ~)
% hObject    handle to tipDiamond (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

% Hints: get(hObject,'String') returns contents of tipDiamond as text
%        str2double(get(hObject,'String')) returns contents of tipDiamond as a double
    load('currentSample.mat')
    currentSample.tip = get(hObject,'String');
    save('currentSample.mat','currentSample');
    clear currentSample
end

% --- Executes during object creation, after setting all properties.
function tipDiamond_CreateFcn(hObject, ~, ~)
% hObject    handle to tipDiamond (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    empty - handles not created until after all CreateFcns called

% Hint: edit controls usually have a white background on Windows.
%       See ISPC and COMPUTER.
if ispc && isequal(get(hObject,'BackgroundColor'), get(0,'defaultUicontrolBackgroundColor'))
    set(hObject,'BackgroundColor','white');
end
end

function sample_Callback(hObject, ~, ~)
% hObject    handle to sample (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

% Hints: get(hObject,'String') returns contents of sample as text
%        str2double(get(hObject,'String')) returns contents of sample as a double
    load('currentSample.mat')
    currentSample.sample = get(hObject,'String');
    save('currentSample.mat','currentSample');
    clear currentSample
end

% --- Executes during object creation, after setting all properties.
function sample_CreateFcn(hObject, ~, ~)
% hObject    handle to sample (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    empty - handles not created until after all CreateFcns called

% Hint: edit controls usually have a white background on Windows.
%       See ISPC and COMPUTER.
if ispc && isequal(get(hObject,'BackgroundColor'), get(0,'defaultUicontrolBackgroundColor'))
    set(hObject,'BackgroundColor','white');
end
end

% --- Executes on button press in buttonStartStopScan.
function buttonStartStopScan_Callback(~, ~, handles)
% hObject    handle to buttonStartStopScan (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)
    if handles.StateControl.state == StateControl.SCANNING
        handles.StateControl.changeToIdleState(handles,1);
    else
        handles.StateControl.changeToScanningState(handles,1);
    end
end

% --- Executes on button press in buttonStartStopLargeXYScan.
function buttonStartStopLargeXYScan_Callback(hObject, ~, handles)
% hObject    handle to buttonStartStopLargeXYScan (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)
    handles.ScanParameters.bEnable = [1 1 0]; % enable xy scan direction
    handles.ScanParameters.MinValues(1)=-100;
    handles.ScanParameters.MaxValues(1)=100;
    handles.ScanParameters.MinValues(2)=-100;
    handles.ScanParameters.MaxValues(2)=100;

    handles.ScanParameters.NPoints(1)= floor(str2double(handles.editXYPoints.String));
    handles.ScanParameters.NPoints(2)= floor(str2double(handles.editXYPoints.String));
%     for k = 1:2                                      % set the current ScanParameters to +/50um    
%         handles.ScanParameters.MinValues(k) = -100;
%         handles.ScanParameters.MaxValues(k) = 100;
%     end
    handles.ScanParameters.DwellTime = str2double(handles.editXYDwell.String); 
    
    % turn zoom-box usage off
    handles.checkUseZoomboxLimits.Value = 0;
    % then start scan as usual
    if handles.StateControl.state == StateControl.SCANNING
        handles.StateControl.changeToIdleState(handles,2);
    else
        handles.StateControl.changeToScanningState(handles,2);
    end
    hObject.String = 'Start Large XY-Scan';
end

% --- Executes on button press in buttonStartStopLocalXYScan.
function buttonStartStopLocalXYScan_Callback(hObject, ~, handles)
% hObject    handle to buttonStartStopLocalXYScan (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)
    handles.ScanParameters.bEnable = [1 1 0]; % enable xy scan direction
    handles.ScanParameters.DwellTime = str2double(handles.editXYDwell.String);
    handles.ScanParameters.NPoints(1)= floor(str2double(handles.editXYPoints.String));
    handles.ScanParameters.NPoints(2)= floor(str2double(handles.editXYPoints.String));
    % turn zoom-box usage on
    handles.checkUseZoomboxLimits.Value = 1;
    % then start scan as usual
    if handles.StateControl.state == StateControl.SCANNING
        handles.StateControl.changeToIdleState(handles,3);
    else
        handles.StateControl.changeToScanningState(handles,3);
    end
    hObject.String = 'Start Local XY-Scan';
    guidata(hObject,handles);
end

% --- Executes on button press in buttonStartStopZScan.
function buttonStartStopZScan_Callback(hObject, ~, handles)
% hObject    handle to buttonStartStopZScan (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)
    
    handles.ScanParameters.bEnable = [0 0 1]; % enable z scan direction
    % this scan will always scan the z direction with the parameters that
    % were set last, either over setup scan button or over the panel next
    % to the z scan, only the dwell time gets read in now from the panel
    handles.ScanParameters.DwellTime = str2double(handles.editZDwell.String);
    handles.ScanParameters.NPoints(3) = str2double(handles.editZPoints.String);
    if handles.StateControl.state == StateControl.SCANNING
        handles.StateControl.changeToIdleState(handles,4);
    else
        handles.StateControl.changeToScanningState(handles,4);
    end
    hObject.String = 'Start Z-Scan';
end

% --- Executes on button press in buttonOpenColormapEditor.
function buttonOpenColormapEditor_Callback(~, ~, handles)
% hObject    handle to buttonOpenColormapEditor (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)
    set(handles.checkboxAutoColorScale, 'Value', 0);
    ax = get(handles.confocalAxes);
    %colormapeditor(ax);
    colormapeditor;
end

% --- Executes on button press in buttonSetupScanFromGUI.
function buttonSetupScanFromGUI_Callback(~, ~, handles)
% hObject    handle to buttonSetupScanFromGUI (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)
     ConfigureScan(handles.ScanParameters,...
        str2double(get(handles.editPositionX,'String')),...
        str2double(get(handles.editPositionY,'String')),handles);
end

% --- Executes on button press in checkboxAutoColorScale.
function checkboxAutoColorScale_Callback(hObject, ~, handles)
% hObject    handle to checkboxAutoColorScale (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

% Hint: get(hObject,'Value') returns toggle state of checkboxAutoColorScale
    if get(hObject, 'Value')==true
        caxis auto;
        colorm = get(handles.confocalAxes, 'UserData');
        colorm.auto = true;
        set(handles.confocalAxes, 'UserData', colorm);
    else
        %If the auto color scale button is being checked off
        colorm = get(handles.confocalAxes, 'UserData');
        colorm.auto = false;
        set(handles.confocalAxes, 'UserData', colorm);
    end
end

% --- Executes on button press in checkboxScanContinuous.
function checkboxScanContinuous_Callback(~, ~, ~)
% hObject    handle to checkboxScanContinuous (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

% Hint: get(hObject,'Value') returns toggle state of checkboxScanContinuous
end

% --- Executes on button press in checkUseZoomboxLimits.
function checkUseZoomboxLimits_Callback(~, ~, ~)
% hObject    handle to checkUseZoomboxLimits (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

% Hint: get(hObject,'Value') returns toggle state of checkUseZoomboxLimits
end

function editZMin_Callback(hObject, ~, handles)
% hObject    handle to editZMin (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

% Hints: get(hObject,'String') returns contents of editZMin as text
%        str2double(get(hObject,'String')) returns contents of editZMin as a double
    handles.ScanParameters.MinValues(3) = str2double(hObject.String);
    guidata(hObject, handles);
end

% --- Executes during object creation, after setting all properties.
function editZMin_CreateFcn(hObject, ~, ~)
% hObject    handle to editZMin (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    empty - handles not created until after all CreateFcns called

% Hint: edit controls usually have a white background on Windows.
%       See ISPC and COMPUTER.
if ispc && isequal(get(hObject,'BackgroundColor'), get(0,'defaultUicontrolBackgroundColor'))
    set(hObject,'BackgroundColor','white');
end
end

function editZMax_Callback(hObject, ~, handles)
% hObject    handle to editZMax (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

% Hints: get(hObject,'String') returns contents of editZMax as text
%        str2double(get(hObject,'String')) returns contents of editZMax as a double
    handles.ScanParameters.MaxValues(3) = str2double(hObject.String);
    guidata(hObject, handles);
end

% --- Executes during object creation, after setting all properties.
function editZMax_CreateFcn(hObject, ~, ~)
% hObject    handle to editZMax (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    empty - handles not created until after all CreateFcns called

% Hint: edit controls usually have a white background on Windows.
%       See ISPC and COMPUTER.
if ispc && isequal(get(hObject,'BackgroundColor'), get(0,'defaultUicontrolBackgroundColor'))
    set(hObject,'BackgroundColor','white');
end
end


function editZPoints_Callback(hObject, ~, handles)
% hObject    handle to editZPoints (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

% Hints: get(hObject,'String') returns contents of editZPoints as text
%        str2double(get(hObject,'String')) returns contents of editZPoints as a double
    if(isnan(str2double(handles.editZPoints.String)) || floor(str2double(handles.editZPoints.String))<1)
     handles.editXYPoints.String="25";
    end
    guidata(hObject, handles);
end

% --- Executes during object creation, after setting all properties.
function editZPoints_CreateFcn(hObject, ~, ~)
% hObject    handle to editZPoints (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    empty - handles not created until after all CreateFcns called

% Hint: edit controls usually have a white background on Windows.
%       See ISPC and COMPUTER.
if ispc && isequal(get(hObject,'BackgroundColor'), get(0,'defaultUicontrolBackgroundColor'))
    set(hObject,'BackgroundColor','white');
end
end


function editZDwell_Callback(~, ~, ~)
% hObject    handle to editZDwell (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

% Hints: get(hObject,'String') returns contents of editZDwell as text
%        str2double(get(hObject,'String')) returns contents of editZDwell as a double
end

% --- Executes during object creation, after setting all properties.
function editZDwell_CreateFcn(hObject, ~, ~)
% hObject    handle to editZDwell (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    empty - handles not created until after all CreateFcns called

% Hint: edit controls usually have a white background on Windows.
%       See ISPC and COMPUTER.
if ispc && isequal(get(hObject,'BackgroundColor'), get(0,'defaultUicontrolBackgroundColor'))
    set(hObject,'BackgroundColor','white');
end
end 

function editXYDwell_Callback(~, ~, ~)
% hObject    handle to editXYDwell (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

% Hints: get(hObject,'String') returns contents of editXYDwell as text
%        str2double(get(hObject,'String')) returns contents of editXYDwell as a double
end

% --- Executes during object creation, after setting all properties.
function editXYDwell_CreateFcn(hObject, ~, ~)
% hObject    handle to editXYDwell (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    empty - handles not created until after all CreateFcns called

% Hint: edit controls usually have a white background on Windows.
%       See ISPC and COMPUTER.
if ispc && isequal(get(hObject,'BackgroundColor'), get(0,'defaultUicontrolBackgroundColor'))
    set(hObject,'BackgroundColor','white');
end
end

% --- Executes on button press in buttonStartStopRunningCount.
function buttonStartStopRunningCount_Callback(~, ~, handles)
% hObject    handle to buttonStartStopRunningCount (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)
    if handles.StateControl.state == StateControl.CURSOR 
        handles.StateControl.changeToIdleState(handles,5);
    else
        handles.StateControl.changeToCursorState(handles,5);
    end
end

%%%%%%%%%%%%%%%%%%%%

% --- Executes on button press in buttonStartTracking.
function buttonStartTracking_Callback(~, ~, handles)
% hObject    handle to buttonStartTracking (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)
    if handles.StateControl.state == StateControl.TRACKING
        handles.StateControl.changeToIdleState(handles,6);
    else
        handles.StateControl.changeToTrackingState(handles,6);
    end
end

% --- Executes on button press in buttonTrackingParameters.
function buttonTrackingParameters_Callback(~, ~, handles)
% hObject    handle to buttonTrackingParameters (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)
    ConfigureTracking(handles);
    guidata(handles.output, handles);
end

%%%%

% --- Executes on button press in buttonResetCountHistory.
function buttonResetCountHistory_Callback(~, ~, handles)
% hObject    handle to buttonResetCountHistory (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)
    handles.CursorControl.resetCountHistory();
end

function editRunningCount_Callback(~, ~, ~)
% hObject    handle to editRunningCount (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

% Hints: get(hObject,'String') returns contents of editRunningCount as text
%        str2double(get(hObject,'String')) returns contents of editRunningCount as a double
end

% --- Executes during object creation, after setting all properties.
function editRunningCount_CreateFcn(hObject, ~, ~)
% hObject    handle to editRunningCount (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    empty - handles not created until after all CreateFcns called

% Hint: edit controls usually have a white background on Windows.
%       See ISPC and COMPUTER.
if ispc && isequal(get(hObject,'BackgroundColor'), get(0,'defaultUicontrolBackgroundColor'))
    set(hObject,'BackgroundColor','white');
end
end

function editCountsToShow_Callback(~, ~, ~)
% hObject    handle to editCountsToShow (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

% Hints: get(hObject,'String') returns contents of editCountsToShow as text
%        str2double(get(hObject,'String')) returns contents of editCountsToShow as a double
end

% --- Executes during object creation, after setting all properties.
function editCountsToShow_CreateFcn(hObject, ~, ~)
% hObject    handle to editCountsToShow (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    empty - handles not created until after all CreateFcns called

% Hint: edit controls usually have a white background on Windows.
%       See ISPC and COMPUTER.
if ispc && isequal(get(hObject,'BackgroundColor'), get(0,'defaultUicontrolBackgroundColor'))
    set(hObject,'BackgroundColor','white');
end
end

% --- Executes on button press in buttonSaveHistory.
function buttonSaveHistory_Callback(~, ~, handles)
% hObject    handle to buttonSaveHistory (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)
    handles.CursorControl.saveCountHistory(handles);
end

function editCountDwellTime_Callback(~, ~, handles)
% hObject    handle to editCountDwellTime (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

% Hints: get(hObject,'String') returns contents of editCountDwellTime as text
%        str2double(get(hObject,'String')) returns contents of editCountDwellTime as a double
    if handles.StateControl.state == StateControl.CURSOR ...
            || handles.StateControl.state == StateControl.TRACKING
        handles.CursorControl.changeDwell();
        handles.CursorControl.runCount(handles);
    end
end

% --- Executes during object creation, after setting all properties.
function editCountDwellTime_CreateFcn(hObject, ~, ~)
% hObject    handle to editCountDwellTime (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    empty - handles not created until after all CreateFcns called

% Hint: edit controls usually have a white background on Windows.
%       See ISPC and COMPUTER.
if ispc && isequal(get(hObject,'BackgroundColor'), get(0,'defaultUicontrolBackgroundColor'))
    set(hObject,'BackgroundColor','white');
end
end

% --- Executes on button press in buttonStartLaserCursor.
function buttonStartLaserCursor_Callback(~, ~, handles)
% hObject    handle to buttonStartLaserCursor (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)
    % We would like to only access this tool if not scanning or tracking
    % it is okay to use it if we are doing a running count, however.
    if handles.StateControl.state ~= handles.StateControl.SCANNING && handles.StateControl.state~=handles.StateControl.TRACKING

        [pointX,pointY] = ginput(1);

        % check if these are within the current limits of the axes. If they are not
        % we interpret this out of bounds click as the user "cancelling" the use of
        % the Laser Cursor ginput.
        xlim = get(handles.confocalAxes,'XLim');
        ylim = get(handles.confocalAxes,'YLim');

        if (pointX>xlim(1) && pointX<xlim(2) && pointY>ylim(1) && pointY<ylim(2))
            set(handles.editPositionX,'String',num2str(pointX));
            set(handles.editPositionY,'String',num2str(pointY));

            % need to update the graphical cursor:
            handles.CursorControl.deleteManualCursor(handles);
            handles.CursorControl.createManualCursor(handles);
            handles.CursorControl.updatePositionFromCursor(handles,[pointX,pointY]);
        end
    end
end

% --- Executes on button press in buttonMakeNVMarker.
function buttonMakeNVMarker_Callback(~, ~, handles)
% hObject    handle to buttonMakeNVMarker (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)
    if handles.StateControl.state ~= handles.StateControl.SCANNING && handles.StateControl.state~=handles.StateControl.TRACKING
        [pointX,pointY] = ginput(1);

        % check if these are within the current limits of the axes. If they are not
        % we interpret this out of bounds click as the user "cancelling" the use of
        % the Laser Cursor ginput.
        xlim = get(handles.confocalAxes,'XLim');
        ylim = get(handles.confocalAxes,'YLim');

        if (pointX>xlim(1) && pointX<xlim(2) && pointY>ylim(1) && pointY<ylim(2))

            currentAxes = handles.confocalAxes;
            numM = str2double(get(handles.NVmarkerNumber,'String'));
            % check if there is already a label for this number
            colorM = 'none'; %#ok<NASGU>
            if get(handles.checkboxMarker15N,'Value')==1
                colorM='red';
                handles.listNVMarkerType(numM) = 15;

            elseif get(handles.checkboxMarker14N,'Value')==1
                colorM='green';
                handles.listNVMarkerType(numM) = 14;
            else
                colorM='none';
                handles.listNVMarkerType(numM) = -1;
            end
            handles.listNVMarkerPos(1,numM) = pointX;
            handles.listNVMarkerPos(2,numM) = pointY;
            if handles.listNVMarkers(numM) ==0

                hold(currentAxes,'on');
                newMark = text(pointX,pointY,['\color{cyan}' get(handles.NVmarkerLetter,'String') get(handles.NVmarkerNumber,'String')],'FontSize',10,'HorizontalAlignment','center','EdgeColor',colorM,'LineStyle',':');
                handles.listNVMarkers(numM) = newMark;
                hold(currentAxes,'off');
            else
               % overwrite the marker.
               delete(handles.listNVMarkers(numM));
               handles.listNVMarkers(numM) = 0;

               hold(currentAxes,'on');
               newMark = text(pointX,pointY,['\color{cyan}' get(handles.NVmarkerLetter,'String') get(handles.NVmarkerNumber,'String')],'FontSize',10,'HorizontalAlignment','center','EdgeColor',colorM,'LineStyle',':');
               handles.listNVMarkers(numM) = newMark;
               hold(currentAxes,'off');
            end

            % increase counter
            set(handles.NVmarkerNumber,'String',num2str(numM+1));
        end
    end
    guidata(handles.output, handles);
end

function NVmarkerLetter_Callback(~, ~, ~)
% hObject    handle to nvmarkerletter (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

% Hints: get(hObject,'String') returns contents of nvmarkerletter as text
%        str2double(get(hObject,'String')) returns contents of nvmarkerletter as a double
end

% --- Executes during object creation, after setting all properties.
function NVmarkerLetter_CreateFcn(hObject, ~, ~)
% hObject    handle to nvmarkerletter (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    empty - handles not created until after all CreateFcns called

% Hint: edit controls usually have a white background on Windows.
%       See ISPC and COMPUTER.
if ispc && isequal(get(hObject,'BackgroundColor'), get(0,'defaultUicontrolBackgroundColor'))
    set(hObject,'BackgroundColor','white');
end
end

function NVmarkerNumber_Callback(hObject, ~, handles)
% hObject    handle to NVmarkerNumber (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

% Hints: get(hObject,'String') returns contents of NVmarkerNumber as text
%        str2double(get(hObject,'String')) returns contents of NVmarkerNumber as a double
    input = floor(str2double(get(hObject,'String')));
    if input > handles.maxLabel
        input = handles.maxLabel;
    end
    set(hObject,'String',num2str(input));
    guidata(handles.output, handles);
    clear input
end

% --- Executes during object creation, after setting all properties.
function NVmarkerNumber_CreateFcn(hObject, ~, ~)
% hObject    handle to NVmarkerNumber (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    empty - handles not created until after all CreateFcns called

% Hint: edit controls usually have a white background on Windows.
%       See ISPC and COMPUTER.
if ispc && isequal(get(hObject,'BackgroundColor'), get(0,'defaultUicontrolBackgroundColor'))
    set(hObject,'BackgroundColor','white');
end
end

% --- Executes on button press in checkboxMarker15N.
function checkboxMarker15N_Callback(~, ~, handles)
% hObject    handle to checkboxMarker15N (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

% Hint: get(hObject,'Value') returns toggle state of checkboxMarker15N
    if get(handles.checkboxMarker14N,'Value')==1
        set(handles.checkboxMarker14N,'Value',0);
    end
    guidata(handles.output, handles);
end

% --- Executes on button press in checkboxMarker14N.
function checkboxMarker14N_Callback(~, ~, handles)
% hObject    handle to checkboxMarker14N (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

% Hint: get(hObject,'Value') returns toggle state of checkboxMarker14N
    if get(handles.checkboxMarker15N,'Value')==1
        set(handles.checkboxMarker15N,'Value',0);
    end
    guidata(handles.output, handles);
end

% --- Executes on button press in buttonDeleteNVMarkers.
function buttonDeleteNVMarkers_Callback(~, ~, handles)
% hObject    handle to buttonDeleteNVMarkers (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)
    askME = questdlg('Are you sure you want to delete all the markers?','Marker deleteion','Yes','No','No');
    switch(askME)
        case 'Yes'
            currentAxes = handles.confocalAxes;
                    ind = find(handles.listNVMarkers(:)); % get all nonzero elements
                    delete(handles.listNVMarkers(ind));
                    handles.listNVMarkers = [];
                    handles.listNVMarkers = zeros(1,handles.maxLabel);
                    handles.listNVMarkerType = zeros(1,handles.maxLabel);
                    handles.listNVMarkerPos = zeros(3,handles.maxLabel);
                    set(handles.NVmarkerNumber,'String',num2str(1));
                %end
                if ~isempty(currentAxes)
                    hold(currentAxes,'off');
                end
                guidata(handles.output, handles);
    end
    clear askME ind
end


% --- Executes on button press in buttonSaveLoadNVMarkers.
function buttonSaveLoadNVMarkers_Callback(hObject, ~, handles)
% hObject    handle to buttonSaveLoadNVMarkers (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)
    selectAction = questdlg('Save markers or load from file onto image?','NV Markers','Save','Load','Cancel','Cancel');

    switch(selectAction)
        case 'Save'
            markersOutput.numMarks = handles.maxLabel;
            for knv = 1:markersOutput.numMarks
               mystr = get(handles.NVmarkerLetter,'String');
               eval(['markersOutput.nvType' num2str(knv) '=' num2str(handles.listNVMarkerType(knv)) ';']);
               eval(['markersOutput.markerLetter' num2str(knv) '= mystr;']);
               eval(['markersOutput.pointX' num2str(knv) '=' num2str(handles.listNVMarkerPos(1,knv)) ';']);
               eval(['markersOutput.pointY' num2str(knv) '=' num2str(handles.listNVMarkerPos(2,knv)) ';']);
            end

            defaultPath = [handles.configS.sequenceFolder 'NVMarkerMaps\'];
            if exist(defaultPath,'dir') ==0 %path does not exist?
                mkdir(defaultPath);
            end
            [filename fpath ~] = uiputfile('*.nvm','Save NV Markers Map As...',defaultPath);

            tdfwrite([fpath filename],markersOutput);

        case 'Load'
            [filename pathname] = uigetfile('*.nvm', 'Select NV Markers Map to Load',...
                        [handles.configS.sequenceFolder 'NVMarkerMaps\']);
            try
                 file1 = [pathname filename];
            catch err %#ok
                 file1 = [];
            end

            %return if no good file is available
            if isempty(file1) || ~ischar(file1)
                return;
            end
            %Check to see that the file is good
            fid = fopen(file1);
            if fid == -1
                return;
            end
            fclose(fid);

            markersOutput = tdfread([pathname filename]);
            numMarks = markersOutput.numMarks;
            for jnv = 1:numMarks
                currentAxes = handles.confocalAxes;
                numM = jnv;
                nvType = eval(['markersOutput.nvType' num2str(numM)]);
                % check if there is already a label for this number
                colorM = 'none'; %#ok<NASGU>
                if nvType==15
                    colorM='red';
                elseif nvType==14
                    colorM='green';
                else
                    colorM='none';
                end
                pointX = eval(['markersOutput.pointX' num2str(numM)]);
                pointY = eval(['markersOutput.pointY' num2str(numM)]);
                markerLetter = eval(['markersOutput.markerLetter' num2str(numM)]);
                set(handles.NVmarkerLetter,'String',markerLetter); % only needs to be set once, but whatever
                handles.listNVMarkerType(1,numM) = nvType;
                if nvType ~= 0 % if 0 then marker is unused and don't create it!!
                    handles.listNVMarkerPos(1,numM) = pointX;
                    handles.listNVMarkerPos(2,numM) = pointY;

                    if handles.listNVMarkers(numM) ==0
                        hold(currentAxes,'on');
                        newMark = text(pointX,pointY,['\color{cyan}' markerLetter num2str(numM)],'FontSize',10,'HorizontalAlignment','center','EdgeColor',colorM,'LineStyle',':');
                        handles.listNVMarkers(numM) = newMark;
                        hold(currentAxes,'off');
                    else
                       % overwrite the marker.
                       handles.listNVMarkers(numM) = 0;
                       hold(currentAxes,'on');
                       newMark = text(pointX,pointY,['\color{cyan}' markerLetter num2str(numM)],'FontSize',10,'HorizontalAlignment','center','EdgeColor',colorM,'LineStyle',':');
                       handles.listNVMarkers(numM) = newMark;
                       hold(currentAxes,'off');
                    end
                    % increase counter
                    set(handles.NVmarkerNumber,'String',num2str(numM+1));
                end
            end
        case 'Cancel' % do nothing
    end
    guidata(handles.output, handles);
    guidata(hObject,handles)
    clear selectAction mystr defaultPath filename fpath pathname file1 fid markersOutput numMarks 
    clear jnv currentAxes numM nvType colorM pointX pointY markerLetter newMark
end

% --- Executes on button press in buttonShiftAllMarkerPosToCurrentNumberPos.
function buttonShiftAllMarkerPosToCurrentNumberPos_Callback(hObject, ~, handles)
% hObject    handle to buttonShiftAllMarkerPosToCurrentNumberPos (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)
    if handles.StateControl.state ~= handles.StateControl.SCANNING && handles.StateControl.state~=handles.StateControl.TRACKING

        % before setting new position save old position
        numM = str2double(get(handles.NVmarkerNumber,'String'));
        if handles.listNVMarkers(numM) == 0 % doesn't exist on image! user cannot shift based on it
            warndlg(['NV marker # ' num2str(numM) ' is non-existent on the image, so it cannot be used as a position reference.'],'Invalid marker operation');
        else
            prevX = handles.listNVMarkerPos(1,numM);
            prevY = handles.listNVMarkerPos(2,numM);

            [pointX,pointY] = ginput(1);
            shiftX = pointX - prevX;
            shiftY = pointY - prevY;

            % check if these are within the current limits of the axes. If they are not
            % we interpret this out of bounds click as the user "cancelling" the use of
            % the Laser Cursor ginput.
            xlim = get(handles.confocalAxes,'XLim');
            ylim = get(handles.confocalAxes,'YLim');

            if (pointX>xlim(1) && pointX<xlim(2) && pointY>ylim(1) && pointY<ylim(2))
                currentAxes = handles.confocalAxes;

                % check if there is already a label for this number
                colorM = 'none'; %#ok<NASGU>
                if get(handles.checkboxMarker15N,'Value')==1
                    colorM='red';
                    handles.listNVMarkerType(numM) = 15;

                elseif get(handles.checkboxMarker14N,'Value')==1
                    colorM='green';
                    handles.listNVMarkerType(numM) = 14;
                else
                    colorM='none';
                    handles.listNVMarkerType(numM) = -1;
                end
                handles.listNVMarkerPos(1,numM) = pointX;
                handles.listNVMarkerPos(2,numM) = pointY;
                if handles.listNVMarkers(numM) ==0
                    hold(currentAxes,'on');
                    newMark = text(pointX,pointY,['\color{cyan}' get(handles.NVmarkerLetter,'String') get(handles.NVmarkerNumber,'String')],'FontSize',10,'HorizontalAlignment','center','EdgeColor',colorM,'LineStyle',':');
                    handles.listNVMarkers(numM) = newMark;
                    hold(currentAxes,'off');
                else
                   % overwrite the marker.
                   delete(handles.listNVMarkers(numM));
                   handles.listNVMarkers(numM) = 0;

                   hold(currentAxes,'on');
                   newMark = text(pointX,pointY,['\color{cyan}' get(handles.NVmarkerLetter,'String') get(handles.NVmarkerNumber,'String')],'FontSize',10,'HorizontalAlignment','center','EdgeColor',colorM,'LineStyle',':');
                   handles.listNVMarkers(numM) = newMark;
                   hold(currentAxes,'off');
                end

                % now go through all the NV markers to update positions
                % first retrieve list of indices for "active" markers (nonzero)
                rInd = find(handles.listNVMarkers);
                for pnv = rInd
                    if pnv ~= numM% do not include the current index for this
                        colorM = 'none'; %#ok<NASGU>
                        if handles.listNVMarkerType(pnv) == 15
                            colorM='red';
                        elseif handles.listNVMarkerType(pnv) == 14
                            colorM='green';
                        else
                            colorM='none';
                        end
                        % overwrite the marker.
                        delete(handles.listNVMarkers(pnv));
                        handles.listNVMarkers(pnv) = 0;

                        newX = handles.listNVMarkerPos(1,pnv) + shiftX;
                        newY = handles.listNVMarkerPos(2,pnv) + shiftY;
                        hold(currentAxes,'on');
                        newMark = text(newX,newY,['\color{cyan}' get(handles.NVmarkerLetter,'String') num2str(pnv)],'FontSize',10,'HorizontalAlignment','center','EdgeColor',colorM,'LineStyle',':');
                        handles.listNVMarkers(pnv) = newMark;
                        hold(currentAxes,'off');
                        % replace old position values in the array
                        handles.listNVMarkerPos(1,pnv) = newX;
                        handles.listNVMarkerPos(2,pnv) = newY;
                    end
                end
            end
        end
    end
    guidata(handles.output, handles);
    guidata(hObject,handles)
    clear numM prevX prevY shiftX shiftY xlim ylim currentAxes colorM newMark rInd pnv newX newY
end




% --- Executes on button press in buttonOpenFreqSweep.
function buttonOpenFreqSweep_Callback(hObject, ~, handles)
% hObject    handle to buttonOpenFreqSweep (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)
    handles.ESRControl = ESRControl(handles.EsrGlobalMethods,...
                                    handles.EsrCWSweep,...
                                    handles.EsrPulsedSweep,...
                                    handles.EsrAutomation);
    guidata(handles.output, handles);
    
    handles.EsrCWSweep.UpdateHandles(handles);
    handles.EsrPulsedSweep.UpdateHandles(handles);
    handles.EsrGlobalMethods.UpdateHandles(handles);
    
    handles.EsrAutomation.UpdateHandles(handles);
    guidata(hObject,handles)
end

function zoomOutCallback(hObject, ~, handles)
%Zooms back out to the maximum size allowed. Double-clicking this button resets the
%scan parameters to the maximum size allowed also.
%tab = get(handles.tabGroup, 'SelectedTab');
set(hObject, 'State', 'off');

xlim = [-100 100];
ylim = [-70 130];
set(handles.confocalAxes, 'XLim', xlim);
set(handles.confocalAxes, 'YLim', ylim);
guidata(hObject,handles)
end


% --- Executes on button press in autosaveCheck.
function autosaveCheck_Callback(hObject, eventdata, handles)
% hObject    handle to autosaveCheck (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

autosaveCheck_state = get(hObject,'Value');
if autosaveCheck_state == get(hObject,'Max')
    handles.configS.bAutoSave = true;
elseif autosaveCheck_state == get(hObject,'Min')
    handles.configS.bAutoSave = false;
end
    
guidata(hObject, handles);

end

function updateGUIsTimer(obj, event, hObject)

updateGUIs(hObject);

end


% --- Executes when user attempts to close ImageScan.
function ImageScan_CloseRequestFcn(hObject, ~, handles)
% hObject    handle to ImageScan (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

% Hint: delete(hObject) closes the figure

stop(handles.guiTimer);
% ziDAQ('unsubscribe',[handles.ApproachGUIHandles.ziDAQ.TipChannelPath '/sample']);
% ziDAQ('flush');


% if (isempty(findobj('Tag', 'scan_gui')))
%     pause(0.1);
%     mDAC('close');
%     pause(1);
%     clear mDAC;
%     delete(findobj('Tag', 'zSet'));
% else
%     delete(findobj('Tag', 'zSet'));
% end

% delete(handles.TransportGUI);
% delete(handles.ApproachGUI);

satCurveFIG=findall(0, 'type', 'figure', 'Name','SaturationCurve');
if(~isempty(satCurveFIG))
   delete(satCurveFIG);
end
delete(handles.srs);
delete(handles.srsNuclear)
delete(handles.srs3)
%delete(handles.lakeshore218)
delete(hObject);
end


% --- Executes during object deletion, before destroying properties.
function ImageScan_DeleteFcn(hObject, eventdata, handles)
% hObject    handle to ImageScan (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

% yn = questdlg('Delete all ImageScan associated figures?',...
%     'Figure Menu',...
%     'Yes','No','No');
% switch yn
%     case 'Yes'    
%         allfigs = findobj(get(groot,'Children'),'Type','figure' );      
%         set(allfigs,'DeleteFcn',[]);
%         delete(allfigs)
%     case 'No'
%         return
% end

end



function editXYPoints_Callback(hObject, eventdata, handles)
% hObject    handle to editXYPoints (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)
if(isnan(str2double(handles.editXYPoints.String)) || floor(str2double(handles.editXYPoints.String))<1)
     handles.editXYPoints.String="50";
end
% Hints: get(hObject,'String') returns contents of editXYPoints as text
%        str2double(get(hObject,'String')) returns contents of editXYPoints as a double
guidata(hObject, handles);
end

% --- Executes during object creation, after setting all properties.
function editXYPoints_CreateFcn(hObject, eventdata, handles)
% hObject    handle to editXYPoints (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    empty - handles not created until after all CreateFcns called

% Hint: edit controls usually have a white background on Windows.
%       See ISPC and COMPUTER.
if ispc && isequal(get(hObject,'BackgroundColor'), get(0,'defaultUicontrolBackgroundColor'))
    set(hObject,'BackgroundColor','white');
end
end


% --- Executes on button press in openSaturationCurve.
function openSaturationCurve_Callback(hObject, eventdata, handles)
% hObject    handle to openSaturationCurve (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)
guidata(hObject,handles);
handles.SaturationCurve = SaturationCurve(hObject);
guidata(hObject,handles);
end


% --- Executes on button press in singleTrackButton.
function singleTrackButton_Callback(hObject, eventdata, handles)
% hObject    handle to singleTrackButton (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)


% if handles.StateControl.state == StateControl.TRACKING
%     handles.StateControl.changeToIdleState(handles,7);
% else
%     handles.StateControl.changeToTrackingState(handles,7,true);
% end


%handles.EsrGlobalMethods.RunSingleTrackCWESR(1,handles);
end


% --- Executes on button press in runScript.
function runScript_Callback(hObject, eventdata, handles)
% hObject    handle to runScript (see GCBO)
% eventdata  reserved - to be defined in a future version of MATLAB
% handles    structure with handles and user data (see GUIDATA)

%Scripts run using this have full access to the ImageScan environment
guidata(hObject,handles);

[file,path]=uigetfile('*.m','Select a script','C:\Coding\Scripts');
run(fullfile(path,file));

%Save any changes made to hObject
guidata(hObject,handles);
end



